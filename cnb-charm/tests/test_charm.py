# Copyright 2021 Ubuntu
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest
from unittest.mock import patch

import json

from charm import CloudNativeBuildpackCharm
from ops.model import ActiveStatus, BlockedStatus, Container, WaitingStatus
from ops.pebble import APIError
from ops.testing import Harness


def mock_pull_api_error(self, path):
    raise APIError({}, -1, "NOT MOCKED", "NOOOOOOO")


def mock_pull_spring_boot_metadata(self, path):
    return Fixture("metadata/spring_boot.toml")


def mock_pull_java_executable_jar_metadata(self, path):
    return Fixture("metadata/java_jar.toml")


def mock_pull_nodejs_http_server_metadata(self, path):
    return Fixture("metadata/nodejs_http_server.toml")


def mock_pull_file_not_found(self, path):
    raise APIError("no", "nope", "NOPE", "No such file or directory")


class Fixture:
    def __init__(self, file_path):
        with open(f"tests/fixtures/{file_path}") as f:
            self.content = f.read()

    def read(self):
        return self.content


class CloudNativeBuildpackCharmTests(unittest.TestCase):

    def setUp(self):
        self.harness = Harness(CloudNativeBuildpackCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.maxDiff = None

    @patch.object(Container, "pull", new=mock_pull_api_error)
    def test_charm_waiting_with_config_without_pebble(self):
        self.harness.update_config({})

        self.assertEqual(self.harness.model.unit.status, WaitingStatus(
            "Waiting for Pebble to initialize in the application container"
        ))

    @patch.object(Container, "pull", new=mock_pull_spring_boot_metadata)
    def test_charm_blocked_with_invalid_config(self):
        self.harness.update_config({
            "environment": "[{yolo"
        })

        self.assertEqual(self.harness.model.unit.status, BlockedStatus(
            "Invalid 'environment' configuration"
        ))

        # Charm stays blocked even if we fire Pebble ready, but the app
        # type will be detected
        container = self.harness.model.unit.get_container("application")
        self.harness.charm.on.application_pebble_ready.emit(container)

        self.assertEqual(self.harness.charm._stored.application_type, "SPRING_BOOT")

        self.assertEqual(self.harness.model.unit.status, BlockedStatus(
            "Invalid 'environment' configuration"
        ))

    @patch.object(Container, "pull", new=mock_pull_file_not_found)
    def test_application_pebble_ready_no_metadata_file(self):
        self.assertIsNone(self.harness.charm._stored.application_type)

        self.harness.update_config({})

        container = self.harness.model.unit.get_container("application")
        self.harness.charm.on.application_pebble_ready.emit(container)

        self.assertEqual(self.harness.charm._stored.application_type, "NOT_CNB")
        self.assertEqual(self.harness.model.unit.status, BlockedStatus(
            "Application not packaged with Cloud Native Buildpacks"
        ))

    @patch.object(Container, "pull", new=mock_pull_spring_boot_metadata)
    def test_application_pebble_ready_spring_boot(self):
        self.assertIsNone(self.harness.charm._stored.application_type)

        self.harness.update_config({})

        container = self.harness.model.unit.get_container("application")

        self.harness.charm.on.application_pebble_ready.emit(container)

        self.assertEqual(self.harness.charm._stored.application_type, "SPRING_BOOT")
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

        service = self.harness.model.unit.get_container("application").get_service("application")
        self.assertTrue(service.is_running())

        updated_plan = self.harness.get_container_pebble_plan("application").to_dict()

        self.assertTrue("environment" not in
                        updated_plan["services"]["application"])

    @patch.object(Container, "pull", new=mock_pull_spring_boot_metadata)
    @patch.object(Container, "push", new=lambda self, path, content: None)
    def test_application_pebble_ready_spring_boot_with_mongodb(self):
        self.assertIsNone(self.harness.charm._stored.application_type)

        rel_id = self.harness.add_relation("mongodb", "mongodb-k8s")
        self.harness.add_relation_unit(rel_id, "mongodb-k8s/0")
        self.harness.update_relation_data(rel_id, "mongodb-k8s", {
            "replica_set_uri": "mongo://test_uri:12345/",
            "replica_set_name": "foobar"
        })

        self.harness.update_config(
            _to_config(
                Fixture("configuration/spring_data_mongodb_required.json")
            )
        )

        container = self.harness.model.unit.get_container("application")

        self.harness.charm.on.application_pebble_ready.emit(container)

        self.assertEqual(self.harness.charm._stored.application_type, "SPRING_BOOT")
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

        service = self.harness.model.unit.get_container("application").get_service("application")
        self.assertTrue(service.is_running())

        updated_plan = self.harness.get_container_pebble_plan("application").to_dict()

        self.assertDictEqual({
            "SPRING_DATA_MONGODB_URI": "mongo://test_uri:12345/"
        }, updated_plan["services"]["application"]["environment"])

    @patch.object(Container, "pull", new=mock_pull_spring_boot_metadata)
    @patch.object(Container, "push", new=lambda self, path, content: None)
    def test_application_pebble_ready_spring_boot_with_missing_mongodb_used(self):
        self.assertIsNone(self.harness.charm._stored.application_type)

        self.harness.update_config(
            _to_config(
                Fixture("configuration/spring_data_mongodb_required.json")
            )
        )

        container = self.harness.model.unit.get_container("application")

        self.harness.charm.on.application_pebble_ready.emit(container)

        self.assertEqual(self.harness.charm._stored.application_type, "SPRING_BOOT")
        self.assertEqual(self.harness.model.unit.status,
                         BlockedStatus("Invalid 'environment' configuration"))

    @patch.object(Container, "pull", new=mock_pull_spring_boot_metadata)
    def test_application_pebble_ready_spring_boot_with_mongodb_not_used(self):
        self.assertIsNone(self.harness.charm._stored.application_type)

        self.harness.update_config(
            _to_config(
                Fixture("configuration/empty_env_and_files.json")
            )
        )

        container = self.harness.model.unit.get_container("application")

        self.harness.charm.on.application_pebble_ready.emit(container)

        self.assertEqual(self.harness.charm._stored.application_type, "SPRING_BOOT")
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

    @patch.object(Container, "pull", new=mock_pull_java_executable_jar_metadata)
    def test_application_pebble_ready_java_executable_jar(self):
        self.assertIsNone(self.harness.charm._stored.application_type)

        self.harness.update_config({})

        container = self.harness.model.unit.get_container("application")

        self.harness.charm.on.application_pebble_ready.emit(container)

        self.assertEqual(self.harness.charm._stored.application_type, "JVM")
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

        service = self.harness.model.unit.get_container("application").get_service("application")
        self.assertTrue(service.is_running())

        updated_plan = self.harness.get_container_pebble_plan("application").to_dict()

        self.assertTrue("environment" not in
                        updated_plan["services"]["application"])

    @patch.object(Container, "pull", new=mock_pull_nodejs_http_server_metadata)
    def test_application_pebble_ready_nodejs_http_server(self):
        self.assertIsNone(self.harness.charm._stored.application_type)

        self.harness.update_config({})

        container = self.harness.model.unit.get_container("application")

        self.harness.charm.on.application_pebble_ready.emit(container)

        self.assertEqual(self.harness.charm._stored.application_type, "UNKNOWN")
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

        service = self.harness.model.unit.get_container("application").get_service("application")
        self.assertTrue(service.is_running())

        updated_plan = self.harness.get_container_pebble_plan("application").to_dict()

        self.assertTrue("environment" not in
                        updated_plan["services"]["application"])

    # def test_evaluate_template_action(self):
    #     rel_id = self.harness.add_relation("mongodb", "mongodb-k8s")
    #     self.harness.add_relation_unit(rel_id, "mongodb-k8s/0")
    #     self.harness.update_relation_data(rel_id, "mongodb-k8s", {
    #         "replica_set_uri": "mongo://test_uri:12345/",
    #         "replica_set_name": "foobar"
    #     })

    #     self.harness.charm.on.


def _to_config(fixture: Fixture):
    parsed_fixture = json.load(fixture)

    return {
        "environment": json.dumps(parsed_fixture["environment"]),
        "files": json.dumps(parsed_fixture["files"])
    }

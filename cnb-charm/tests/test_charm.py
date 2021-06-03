# Copyright 2021 Ubuntu
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest
from unittest.mock import patch

from charm import CloudNativeBuildpackCharm
from ops.model import ActiveStatus, BlockedStatus, Container
from ops.pebble import APIError
from ops.testing import Harness


def mock_pull_spring_boot_metadata(self, path):
    return ConfigFile("tests/fixtures/metadata/spring_boot.toml")


def mock_pull_java_executable_jar_metadata(self, path):
    return ConfigFile("tests/fixtures/metadata/java_jar.toml")


def mock_pull_file_not_found(self, path):
    raise APIError("no", "nope", "NOPE", "No such file or directory")


class ConfigFile:
    def __init__(self, file_path):
        with open(file_path) as f:
            self.content = f.read()

    def read(self):
        return self.content


class CloudNativeBuildpackCharmTests(unittest.TestCase):

    def setUp(self):
        self.harness = Harness(CloudNativeBuildpackCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.maxDiff = None

    @patch.object(Container, "pull", new=mock_pull_file_not_found)
    def test_application_pebble_ready_no_metadata_file(self):
        self.assertIsNone(self.harness.charm._stored.application_type)

        container = self.harness.model.unit.get_container("application")
        self.harness.charm.on.application_pebble_ready.emit(container)

        self.assertEqual(self.harness.charm._stored.application_type, "NOT_CNB")
        self.assertEqual(self.harness.model.unit.status, BlockedStatus(
            "Application not packaged with Cloud Native Buildpacks"
        ))

    @patch.object(Container, "pull", new=mock_pull_spring_boot_metadata)
    def test_application_pebble_ready_spring_boot(self):
        self.assertIsNone(self.harness.charm._stored.application_type)

        # Check the initial Pebble plan is empty
        initial_plan = self.harness.get_container_pebble_plan("application")
        self.assertEqual(initial_plan.to_yaml(), "{}\n")
        # Expected plan after Pebble ready with default config

        container = self.harness.model.unit.get_container("application")
        # Emit the PebbleReadyEvent carrying the httpbin container
        self.harness.charm.on.application_pebble_ready.emit(container)

        # Check the service was started
        self.assertEqual(self.harness.charm._stored.application_type, "SPRING_BOOT")
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

        service = self.harness.model.unit.get_container("application").get_service("application")
        self.assertTrue(service.is_running())

        # Get the plan now we've run PebbleReady
        updated_plan = self.harness.get_container_pebble_plan("application").to_dict()
        # Check we've got the plan we expected
        self.assertTrue("environment" not in
                        updated_plan["services"]["application"])

    @patch.object(Container, "pull", new=mock_pull_spring_boot_metadata)
    def test_application_pebble_ready_spring_boot_with_mongodb(self):
        self.assertIsNone(self.harness.charm._stored.application_type)

        # pretend to have a mongodb relation
        self.harness.charm._stored.mongodb_uri = 'mongo://test_uri:12345/'

        # Check the initial Pebble plan is empty
        initial_plan = self.harness.get_container_pebble_plan("application")
        self.assertEqual(initial_plan.to_yaml(), "{}\n")
        # Expected plan after Pebble ready with default config

        container = self.harness.model.unit.get_container("application")
        # Emit the PebbleReadyEvent carrying the httpbin container
        self.harness.charm.on.application_pebble_ready.emit(container)

        # Check the service was started
        self.assertEqual(self.harness.charm._stored.application_type, "SPRING_BOOT")
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

        service = self.harness.model.unit.get_container("application").get_service("application")
        self.assertTrue(service.is_running())

        # Get the plan now we've run PebbleReady
        updated_plan = self.harness.get_container_pebble_plan("application").to_dict()
        # Check we've got the plan we expected
        self.assertDictEqual({
            "SPRING_DATA_MONGODB_HOST": "test_uri",
            "SPRING_DATA_MONGODB_PORT": 12345
        }, updated_plan["services"]["application"]["environment"])

    @patch.object(Container, "pull", new=mock_pull_java_executable_jar_metadata)
    def test_application_pebble_ready_java_executable_jar(self):
        self.assertIsNone(self.harness.charm._stored.application_type)

        # Check the initial Pebble plan is empty
        initial_plan = self.harness.get_container_pebble_plan("application")
        self.assertEqual(initial_plan.to_yaml(), "{}\n")
        # Expected plan after Pebble ready with default config

        container = self.harness.model.unit.get_container("application")
        # Emit the PebbleReadyEvent carrying the httpbin container
        self.harness.charm.on.application_pebble_ready.emit(container)

        # Check the service was started
        self.assertEqual(self.harness.charm._stored.application_type, "JVM")
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

        service = self.harness.model.unit.get_container("application").get_service("application")
        self.assertTrue(service.is_running())

        # Get the plan now we've run PebbleReady
        updated_plan = self.harness.get_container_pebble_plan("application").to_dict()
        # Check we've got the plan we expected
        self.assertTrue("environment" not in
                        updated_plan["services"]["application"])

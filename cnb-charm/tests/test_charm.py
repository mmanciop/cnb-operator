# Copyright 2021 Ubuntu
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import json
import unittest
from unittest.mock import Mock, patch

from charm import CloudNativeBuildpackCharm
from ops.model import ActiveStatus, BlockedStatus, Container, MaintenanceStatus
from ops.pebble import APIError
from ops.testing import Harness


def mock_pull_api_error(self, *args, **kwargs):
    raise APIError({}, -1, "NOT MOCKED", "NOOOOOOO")


def mock_pull_spring_boot_metadata(self, *args, **kwargs):
    return Fixture("metadata/spring_boot.toml")


def mock_pull_java_executable_jar_metadata(self, *args, **kwargs):
    return Fixture("metadata/java_jar.toml")


def mock_pull_nodejs_http_server_metadata(self, *args, **kwargs):
    return Fixture("metadata/nodejs_http_server.toml")


def mock_pull_file_not_found(self, *args, **kwargs):
    raise APIError("no", "nope", "NOPE", "No such file or directory")


class Fixture:
    def __init__(self, file_path):
        with open(f"tests/fixtures/{file_path}") as f:
            self.content = f.read()

    def read(self):
        return self.content


def _fixture_as_str(fixture_path: str):
    return json.loads(Fixture(fixture_path).read())


class CloudNativeBuildpackCharmTests(unittest.TestCase):

    def init_harness(self, meta=None):
        self.harness = Harness(
            charm_cls=CloudNativeBuildpackCharm,
            meta=meta
        )
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()
        self.maxDiff = None

    @patch.object(Container, "pull", new=mock_pull_api_error)
    def test_charm_waiting_for_pebble(self):
        self.init_harness(meta=Fixture(
            "metadata.yaml/spring_data_mongodb_manifest.yaml"
        ).read())

        self.assertEqual(self.harness.model.unit.status, MaintenanceStatus(
            "Waiting for Pebble to initialize in the application container"
        ))

    @patch.object(Container, "pull", new=mock_pull_file_not_found)
    @patch.object(CloudNativeBuildpackCharm, "_get_configs",
                  new=lambda x: _fixture_as_str(
                      "metadata.yaml/spring_data_mongodb_config.json"
                  ))
    def test_application_pebble_ready_no_metadata_file(self):
        self.init_harness(meta=Fixture(
            "metadata.yaml/spring_data_mongodb_manifest.yaml"
        ).read())

        self.assertIsNone(self.harness.charm._stored.application_type)

        container = self.harness.model.unit.get_container("application")
        self.harness.charm.on.application_pebble_ready.emit(container)

        self.assertEqual(self.harness.charm._stored.application_type, "NOT_CNB")
        self.assertEqual(self.harness.model.unit.status, BlockedStatus(
            "Application not packaged with Cloud Native Buildpacks"
        ))

    @patch.object(Container, "pull", new=mock_pull_spring_boot_metadata)
    @patch.object(Container, "push", new=lambda self, path, content: None)
    @patch.object(CloudNativeBuildpackCharm, "_get_configs",
                  new=lambda x: _fixture_as_str(
                      "metadata.yaml/spring_data_mongodb_config.json"
                  ))
    def test_application_pebble_ready_with_all_relations_missing(self):
        self.init_harness(meta=Fixture(
            "metadata.yaml/spring_data_mongodb_manifest.yaml"
        ).read())

        # TODO Wire environment and files

        self.assertIsNone(self.harness.charm._stored.application_type)

        rel_id = self.harness.add_relation("database", "mongodb-k8s")
        self.harness.add_relation_unit(rel_id, "mongodb-k8s/0")
        self.harness.update_relation_data(rel_id, "mongodb-k8s", {
            "replica_set_uri": "mongo://test_uri:12345/",
            "replica_set_name": "foobar"
        })

        container = self.harness.model.unit.get_container("application")

        self.harness.charm.on.application_pebble_ready.emit(container)

        self.assertEqual(self.harness.charm._stored.application_type, "SPRING_BOOT")
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

        service = self.harness.model.unit.get_container("application").get_service("application")
        self.assertTrue(service.is_running())

        pebble_plan = self.harness.get_container_pebble_plan("application")
        application_service = pebble_plan.services["application"]
        application_environment = application_service.environment

        self.assertEqual(application_environment["SPRING_DATA_MONGODB_URI"],
                         "mongo://test_uri:12345/")

    @patch.object(Container, "pull", new=mock_pull_spring_boot_metadata)
    @patch.object(Container, "push", new=lambda self, path, content: None)
    @patch.object(CloudNativeBuildpackCharm, "_get_configs",
                  new=lambda x: _fixture_as_str(
                      "metadata.yaml/spring_data_mongodb_and_jaeger_config.json"
                  ))
    def test_application_pebble_ready_some_relations_missing(self):
        self.init_harness(meta=Fixture(
            "metadata.yaml/spring_data_mongodb_and_jaeger_manifest.yaml"
        ).read())

        # TODO Wire environment and files

        self.assertIsNone(self.harness.charm._stored.application_type)

        rel_id = self.harness.add_relation("database", "mongodb-k8s")
        self.harness.add_relation_unit(rel_id, "mongodb-k8s/0")
        self.harness.update_relation_data(rel_id, "mongodb-k8s", {
            "replica_set_uri": "mongo://test_uri:12345/",
            "replica_set_name": "foobar"
        })

        container = self.harness.model.unit.get_container("application")

        self.harness.charm.on.application_pebble_ready.emit(container)

        self.assertEqual(self.harness.charm._stored.application_type, "SPRING_BOOT")
        self.assertEqual(self.harness.model.unit.status, BlockedStatus(
            "Required consumed relations are missing: distributed-tracing"
        ))

    @patch.object(Container, "pull", new=mock_pull_spring_boot_metadata)
    @patch.object(Container, "push", new=lambda self, path, content: None)
    @patch.object(CloudNativeBuildpackCharm, "_get_configs",
                  new=lambda x: _fixture_as_str(
                      "metadata.yaml/spring_data_mongodb_config.json"
                  ))
    def test_missing_relation(self):
        self.init_harness(meta=Fixture(
            "metadata.yaml/spring_data_mongodb_manifest.yaml"
        ).read())

        container = self.harness.model.unit.get_container("application")

        self.harness.charm.on.application_pebble_ready.emit(container)

        self.assertEqual(self.harness.model.unit.status,
                         BlockedStatus("Required consumed relations are "
                                       "missing: database"))

    @patch.object(Container, "pull", new=mock_pull_spring_boot_metadata)
    @patch.object(Container, "push", new=lambda self, path, content: None)
    @patch.object(CloudNativeBuildpackCharm, "_get_configs",
                  new=lambda x: _fixture_as_str(
                      "metadata.yaml/jaeger_config.json"
                  ))
    def test_rendered_templates(self):
        self.init_harness(meta=Fixture(
            "metadata.yaml/jaeger_manifest.yaml"
        ).read())

        self.assertIsNone(self.harness.charm._stored.application_type)

        rel_id = self.harness.add_relation("jaeger", "jaeger")
        self.harness.add_relation_unit(rel_id, "jaeger/0")
        self.harness.update_relation_data(rel_id, "jaeger/0", {
            "agent-address": "10.1.241.157",
            "port": "6831",
            "port_binary": "6832"
        })

        container = self.harness.model.unit.get_container("application")

        self.harness.charm.on.application_pebble_ready.emit(container)

        pebble_plan = self.harness.get_container_pebble_plan("application")
        application_service = pebble_plan.services["application"]
        application_environment = application_service.environment

        self.assertEqual(application_environment["JAEGER_AGENT_HOST"],
                         "10.1.241.157")
        self.assertEqual(application_environment["JAEGER_AGENT_PORT"],
                         "6831")

        self.assertEqual(self.harness.charm._stored.application_type, "SPRING_BOOT")
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

    @patch.object(Container, "pull", new=mock_pull_spring_boot_metadata)
    @patch.object(Container, "push", new=lambda self, path, content: None)
    @patch.object(CloudNativeBuildpackCharm, "_get_configs",
                  new=lambda x: _fixture_as_str(
                      "metadata.yaml/spring_data_mongodb_config.json"
                  ))
    def test_evaluate_template_action_success(self):
        self.init_harness(meta=Fixture(
            "metadata.yaml/spring_data_mongodb_manifest.yaml"
        ).read())

        rel_id = self.harness.add_relation("database", "mongodb-k8s")
        self.harness.add_relation_unit(rel_id, "mongodb-k8s/0")
        self.harness.update_relation_data(rel_id, "mongodb-k8s", {
            "replica_set_uri": "mongo://test_uri:12345/",
            "replica_set_name": "foobar"
        })

        action_event = Mock(
            params={
                "template": "{{relations.consumes.database.app.replica_set_uri}}"
            }
        )

        self.harness.charm._on_evaluate_template_action(action_event)

        self.assertTrue(action_event.set_results.called)

    @patch.object(Container, "pull", new=mock_pull_spring_boot_metadata)
    @patch.object(Container, "push", new=lambda self, path, content: None)
    @patch.object(CloudNativeBuildpackCharm, "_get_configs",
                  new=lambda x: _fixture_as_str(
                      "metadata.yaml/spring_data_mongodb_config.json"
                  ))
    def test_evaluate_template_action_malformed_template(self):
        self.init_harness(meta=Fixture(
            "metadata.yaml/spring_data_mongodb_manifest.yaml"
        ).read())

        rel_id = self.harness.add_relation("database", "mongodb-k8s")
        self.harness.add_relation_unit(rel_id, "mongodb-k8s/0")
        self.harness.update_relation_data(rel_id, "mongodb-k8s", {
            "replica_set_uri": "mongo://test_uri:12345/",
            "replica_set_name": "foobar"
        })

        action_event = Mock(params={"template": "{{ "})
        self.harness.charm._on_evaluate_template_action(action_event)

        self.assertEqual(action_event.fail.call_args,
                         [("Action 'evaluate-template' failed: "
                           "unexpected 'end of template'",)])

    @patch.object(Container, "pull", new=mock_pull_spring_boot_metadata)
    @patch.object(Container, "push", new=lambda self, path, content: None)
    @patch.object(CloudNativeBuildpackCharm, "_get_configs",
                  new=lambda x: _fixture_as_str(
                      "metadata.yaml/spring_data_mongodb_config.json"
                  ))
    def test_evaluate_dump_template_globals_success(self):
        self.init_harness(meta=Fixture(
            "metadata.yaml/spring_data_mongodb_manifest.yaml"
        ).read())

        rel_id = self.harness.add_relation("database", "mongodb-k8s")
        self.harness.add_relation_unit(rel_id, "mongodb-k8s/0")
        self.harness.update_relation_data(rel_id, "mongodb-k8s", {
            "replica_set_uri": "mongo://test_uri:12345/",
            "replica_set_name": "foobar"
        })

        action_event = Mock()

        self.harness.charm._on_dump_template_globals_action(action_event)

        self.assertTrue(action_event.set_results.called)

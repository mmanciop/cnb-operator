# Copyright 2021 Ubuntu
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest
from unittest.mock import Mock

from charm import HelloWorldCharm
from ops.model import ActiveStatus
from ops.testing import Harness


class CloudNativeBuildpackCharm(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(HelloWorldCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_application_pebble_ready(self):
        # Check the initial Pebble plan is empty
        initial_plan = self.harness.get_container_pebble_plan("application")
        self.assertEqual(initial_plan.to_yaml(), "{}\n")
        # Expected plan after Pebble ready with default config

        expected_plan = {
            "summary": "cnb lifecycle layer",
            "description": "Pebble service layer to start the application",
            "services": {
                "application": {
                    "override": "replace",
                    "summary": "Bootstraps the Cloud Native Buildpack lifecycle",
                    "command": "/cnb/process/web",
                    "startup": "enabled",
                }
            },
        }
        # Get the httpbin container from the model
        container = self.harness.model.unit.get_container("application")
        # Emit the PebbleReadyEvent carrying the httpbin container
        self.harness.charm.on.httpbin_pebble_ready.emit(container)
        # Get the plan now we've run PebbleReady
        updated_plan = self.harness.get_container_pebble_plan("application").to_dict()
        # Check we've got the plan we expected
        self.assertEqual(expected_plan, updated_plan)
        # Check the service was started
        service = self.harness.model.unit.get_container("application").get_service("application")
        self.assertTrue(service.is_running())
        # Ensure we set an ActiveStatus with no message
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())

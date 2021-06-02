#!/usr/bin/env python3
# Copyright 2021 Ubuntu
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

    https://discourse.charmhub.io/t/4208
"""

import logging

from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus
from ops.pebble import ConnectionError

from urllib.parse import urlparse

logger = logging.getLogger(__name__)

CNB_LIFECYCLE_WEB_PATH="/cnb/process/web"

# TODOs:
#
# * Watchdog the application, if it crashes silently Pebble won't currently auto-restart it

class CloudNativeBuildpackCharm(CharmBase):
    """Charm the service."""

    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)

        self.framework.observe(self.on.application_pebble_ready, self._on_application_pebble_ready)

        self.framework.observe(self.on.config_changed, self._on_config_changed)

        self.framework.observe(self.on["mongodb"].relation_changed, self._on_mongodb_relation_changed)
        self.framework.observe(self.on["mongodb"].relation_broken, self._on_mongodb_relation_broken)

        self._stored.set_default(mongodb_uri=str())  # connection info for mongodb
        self._stored.set_default(current_environment={})  # connection info for mongodb

    def _on_application_pebble_ready(self, event):
        """Define and start the Cloud Native Buildpack lifecycle using the Pebble API.
        """

        self._update_cnb_lyfecycle_layer_and_restart_application("Application container initialized")

    def _on_config_changed(self, event):
        self._update_cnb_lyfecycle_layer_and_restart_application("Configuration changed")

    def _on_mongodb_relation_changed(self, event):
        self.unit.status = MaintenanceStatus(f'Processing changes in the \'mongodb\' relation')

        data = event.relation.data[event.unit]
        replica_set_uri = data.get("replica_set_uri")
        replica_set_name = data.get("replica_set_name")

        missing_relation_data = []
        if replica_set_uri is None:
            missing_relation_data.append("replica_set_uri")

        if replica_set_name is None:
            missing_relation_data.append("replica_set_name")

        if missing_relation_data:
            self.unit.status = BlockedStatus(f"'mongodb' relation data is incomplete, the following data are missing: {', '.join(missing_relation_data)}")
            return

        self._stored.mongodb_uri = replica_set_uri

        logger.debug("Updated MongoDB URI configuration: %s", replica_set_uri)

        try:
            self._update_cnb_lyfecycle_layer_and_restart_application("MongoDB relation created")
        except ConnectionError: # Pebble not ready yet
            logger.debug("Deferring update of MongoDB configurations, Pebble is not ready yet in the 'application' container")
            event.defer()
            return

    def _on_mongodb_relation_broken(self, event):
        self._stored.mongodb_uri = str()
        logger.debug("Removed MongoDB URI configuration")

        try:
            self._update_cnb_lyfecycle_layer_and_restart_application("MongoDB relation removed")
        except ConnectionError: # Pebble not ready yet
            logger.debug("Deferring update of MongoDB configurations, Pebble is not ready yet in the 'application' container")
            event.defer()
            return

    def _update_cnb_lyfecycle_layer_and_restart_application(self, reason):
        self.unit.status = MaintenanceStatus(f'Configuring the application: {reason}')

        application_container = self.unit.get_container("application")

        new_environment = {}

        if self._stored.mongodb_uri:
            mongodb_uri = self._stored.mongodb_uri

            parse_result = urlparse(mongodb_uri)

            hostname = parse_result.hostname
            port = parse_result.port

            new_environment["SPRING_DATA_MONGODB_HOST"] = hostname
            new_environment["SPRING_DATA_MONGODB_PORT"] = port

            logger.debug("Adding Spring Data MongoDB Host and Port to the environment: netloc: %s; port: %s", hostname, port)

        application_container.add_layer("cnb_lifecycle", {
            "summary": "cnb lifecycle layer",
            "description": "Pebble service layer to start the application",
            "services": {
                "application": {
                    "override": "replace",
                    "summary": "Bootstraps the Cloud Native Buildpack lifecycle",
                    "command": CNB_LIFECYCLE_WEB_PATH,
                    "environment": new_environment,
                    "startup": "enabled",
                }
            },
        }, combine=True)

        logger.debug("Layer 'cnb_lifecycle' updated")

        log_start = True
        if application_container.get_service("application").is_running():
            if new_environment == self._stored.current_environment:
                logger.debug("No changes in configuration detected, the application will not be restarted")
            else:
                logger.info("Restarting the application to apply environment configuration changes")
                log_start = False

                application_container.stop("application")

        if not application_container.get_service("application").is_running():
            if log_start is True:
                logger.info("Starting the application")

            logger.debug("Application environment based on configurations and relations: %s", new_environment)

            application_container.start("application")
            logger.debug("Application started")

            self._stored.current_environment = new_environment
            logger.debug("Current environment updated to: %s", new_environment)

        self.unit.status = ActiveStatus()


if __name__ == "__main__":
    main(CloudNativeBuildpackCharm)

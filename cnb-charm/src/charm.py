#!/usr/bin/env python3
# Copyright 2021 Ubuntu
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

import logging
import toml

from enum import Enum

from ops.charm import CharmBase, RelationJoinedEvent
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus
from ops.pebble import APIError

from urllib.parse import urlparse

logger = logging.getLogger(__name__)

CNB_METADATA_PATH = "/layers/config/metadata.toml"
CNB_LIFECYCLE_WEB_PATH = "/cnb/process/web"


class ApplicationType(Enum):
    NOT_CNB = -1
    UNKNOWN = 0
    JVM = 1
    SPRING_BOOT = 2
    # NODE_JS = 10
    # PYTHON = 20
    # RUBY = 30
    # DOT_NET = 40


# TODOs:
#
# * Watchdog the application, if it crashes silently
#   Pebble won't currently auto-restart it
#
# * Find out in which scenarios we should rather invoke
#   /cnb/process/executable-jar or /cnb/process/task
class CloudNativeBuildpackCharm(CharmBase):
    """Charm applications packages with Cloud Native Buildpacks"""

    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)

        self.framework.observe(self.on.application_pebble_ready,
                               self._on_application_pebble_ready)

        self.framework.observe(self.on.config_changed,
                               self._on_config_changed)

        self.framework.observe(self.on["mongodb"].relation_joined,
                               self._on_mongodb_relation_upserted)
        self.framework.observe(self.on["mongodb"].relation_changed,
                               self._on_mongodb_relation_upserted)
        self.framework.observe(self.on["mongodb"].relation_broken,
                               self._on_mongodb_relation_broken)

        # is it Spring Boot, something else on JVM, or Node.js, etc.
        self._stored.set_default(application_type=None)
        # connection info for mongodb
        self._stored.set_default(mongodb_uri=str())
        # connection info for mongodb
        self._stored.set_default(current_environment={})

    def _on_application_pebble_ready(self, event):
        """Define and start the Cloud Native Buildpack lifecycle
           using the Pebble API.
        """

        self._determine_application_type()

        self._update_cnb_lyfecycle_layer_and_restart_application(
            "Application container initialized"
        )

    def _on_config_changed(self, event):
        # Nothing to do until pebble is ready and we can inspect
        # the application container
        if self._stored.application_type is None:
            logger.debug("Pebble has not yet determined whether "
                         "the application container has been "
                         "built with Cloud Native Buildpacks")
            event.defer()
            return

        self._update_cnb_lyfecycle_layer_and_restart_application(
            "Configuration changed"
        )

    def _on_mongodb_relation_upserted(self, event):
        data = event.relation.data[event.unit]
        replica_set_uri = data.get("replica_set_uri")
        replica_set_name = data.get("replica_set_name")

        missing_relation_data = []
        if replica_set_uri is None:
            missing_relation_data.append("replica_set_uri")

        if replica_set_name is None:
            missing_relation_data.append("replica_set_name")

        if missing_relation_data:
            self.unit.status = BlockedStatus(
                "'mongodb' relation data is incomplete,"
                " the following data are missing: "
                f"{', '.join(missing_relation_data)}")
            return

        self._stored.mongodb_uri = replica_set_uri

        logger.debug("Updated MongoDB URI configuration: %s", replica_set_uri)

        # Nothing else to do until pebble is ready and we can inspect
        # the application container
        if self._stored.application_type is None:
            logger.debug("Pebble has not yet determined whether "
                         "the application container has been "
                         "built with Cloud Native Buildpacks")
            return

        message = "MongoDB relation changed"
        if isinstance(event, RelationJoinedEvent):
            message = "MongoDB relation created"

        self._update_cnb_lyfecycle_layer_and_restart_application(
            message
        )

    def _on_mongodb_relation_broken(self, event):
        self._stored.mongodb_uri = str()
        logger.debug("Removed MongoDB URI configuration")

        # Nothing to do until pebble is ready and we can inspect the application container
        if self._stored.application_type is None:
            logger.debug("Pebble has not yet determined whether the application "
                         "container has been built with Cloud Native Buildpacks")
            return

        self._update_cnb_lyfecycle_layer_and_restart_application(
            "MongoDB relation removed"
        )

    def _determine_application_type(self):
        """ Check if it is a Buildpack application (by looking for the
            `${LAYERS_DIR}/config/metadata.toml` file) and,
            if found, which type of app it is
        """

        application_container = self.unit.get_container("application")
        # TODO Support lookup of the ${LAYERS_DIR} value when we can
        #      execute commands via Pebble

        parsed_metadata = None
        try:
            metadata_file = application_container.pull(CNB_METADATA_PATH)

            parsed_metadata = toml.loads(metadata_file.read())
        except APIError as e:
            if "No such file or directory" in str(e):
                logger.debug("'%s' file not found in the application container",
                             CNB_METADATA_PATH)
        except Exception:
            logger.exception("An error occurred while looking in the application "
                             "container for the '%s' file", CNB_METADATA_PATH)

        finally:
            if parsed_metadata is None:
                self._stored.application_type = ApplicationType.NOT_CNB.name
                return

        # OK, it looks like a CNB image
        self._stored.application_type = ApplicationType.UNKNOWN.name

        # TODO Look into multi-process CNBs
        if parsed_metadata["processes"]:
            for process in parsed_metadata["processes"]:
                if process["command"] == "java":
                    self._stored.application_type = ApplicationType.JVM.name
                    if process["args"] and \
                       "org.springframework.boot.loader.JarLauncher" in process["args"]:

                        self._stored.application_type = ApplicationType.SPRING_BOOT.name

        logger.info("Detected a %s application", self._stored.application_type)

    def _update_cnb_lyfecycle_layer_and_restart_application(self, reason):
        """This is the most central part of the charm. This method must be invoked
           by pretty much every callback for every event that will require to either
           start or restart the application
        """

        if self._stored.application_type is None:
            raise Exception("'_update_cnb_lyfecycle_layer_and_restart_application' "
                            "invoked before the type of the application type is known!")

        if ApplicationType.NOT_CNB.name == self._stored.application_type:
            logger.debug("This application does not seem to have been packaged with "
                         "Cloud Native Buildpacks; skipping layer update and "
                         "application launch")

            self.unit.status = BlockedStatus(
                "Application not packaged with Cloud Native Buildpacks"
            )
            return

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

            logger.debug(
                "Adding Spring Data MongoDB Host and Port to the environment: "
                "hostname: %s; port: %s", hostname, port
            )

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
                logger.debug(
                    "No changes in configuration detected, the application "
                    "will not be restarted"
                )
            else:
                logger.info(
                    "Restarting the application to apply environment "
                    "configuration changes"
                )
                log_start = False

                application_container.stop("application")

        if not application_container.get_service("application").is_running():
            if log_start is True:
                logger.info("Starting the application")

            logger.debug("Application environment based on configurations and relations: %s",
                         new_environment)

            application_container.start("application")
            logger.debug("Application started")

            self._stored.current_environment = new_environment
            logger.debug("Current environment updated to: %s", new_environment)

        self.unit.status = ActiveStatus()


if __name__ == "__main__":
    main(CloudNativeBuildpackCharm)

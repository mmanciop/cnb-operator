#!/usr/bin/env python3
# Copyright 2021 Ubuntu
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

import logging
import json
import toml

from enum import Enum
from jinja2 import Environment

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

        logger.debug("Updated MongoDB URI configuration: "
                     "replica_set_uri: %s; replica_set_name: %s",
                     replica_set_uri, replica_set_name)

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
        logger.debug("Removed MongoDB URI configuration")

        # Nothing to do until pebble is ready and we can inspect the
        # application container
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

        template_globals = {
            "relations": {
                "consumed": {}
            }
        }

        # Check required relations
        # TODO Use JSON schema for this?
        if self.model.config["consumed_relations"]:
            consumed_relations = []

            try:
                consumed_relations = json.loads(self.model.config["consumed_relations"])

                if not type(consumed_relations) == list:
                    raise Exception("The 'consumed_relations' configuration is expected "
                                    "to contain a stringified JSON array; "
                                    f"found a '{type(consumed_relations)}' instead")

                for index, consumed_relation in enumerate(consumed_relations):
                    if not type(consumed_relation) == dict:
                        raise Exception(f"The item number {index} of the 'consumed_relations' "
                                        "configuration is expected to contain a JSON object; "
                                        f"found a '{type(consumed_relation)}' instead")

                    if not consumed_relation["name"]:
                        raise Exception("The required field 'name' is missing from the item "
                                        f"number {index} of the 'consumed_relations' "
                                        "configuration")

            except Exception:
                logger.exception("An error occurred while parsing the 'consumed_relations' "
                                 "configuration")
                self.unit.status = BlockedStatus("Invalid 'consumed_relations' configuration")
                return

            for consumed_relation in consumed_relations:
                relation_name = consumed_relation["name"]
                relation_required = consumed_relation["required"]

                relations = self.model.relations[relation_name]

                if len(relations) == 0:
                    if relation_required is True or relation_required == "true":
                        # TODO What about multiple relations of the same type?

                        self.unit.status = BlockedStatus(
                            f"The required consumed '{relation_name}' relation is missing")
                        return

                    # Relation is optional, skip
                    continue

                # if len(relations) > 1:
                #     self.unit.status = BlockedStatus(
                #         "There is more than one instance of the required consumed relation "
                #         f"'{relation_name}'; only one instance of a relation is currently "
                #         "supported")
                #     return

                # relation = relations[0]

                # if relation.name == "mongodb":

                    # Need to read the standalone_uri from the unit data
                    # TODO Fix this when the mongodb-k8s operator gets updated

                    # logger.error(f"MongoDB data: {relation.data}")
                    # logger.error(f"MongoDB units: {relation.units}")
                    # logger.error(f"MongoDB app: {relation.app}")

                    # if "replica_set_uri" not in relation.data:
                    #     raise Exception("The 'mongodb' relation does not contain the "
                    #                     "expected 'replica_set_uri' key")

                    # mongodb_uri = relation.data["replica_set_uri"]
                    # parse_result = urlparse(mongodb_uri)

                    # template_globals["relations"]["consumed"]["mongodb"] = {
                    #     "hostname": parse_result.hostname,
                    #     "port": parse_result.port
                    # }

                    # logger.debug(
                    #     "Added MongoDB relation data to template globals: "
                    #     "hostname: %s; port: %s", parse_result.hostname, parse_result.port
                    # )

        template_environment = Environment()
        new_environment = {}

        if self.model.config["environment"]:
            environment_variables = []

            try:
                environment = json.loads(self.model.config["environment"])

                if not type(environment) == list:
                    raise Exception("The 'environment' configuration is expected "
                                    "to contain a stringified JSON array; "
                                    f"found a '{type(environment)}' instead")

                for index, environment_variable in enumerate(environment):
                    if not type(environment_variable) == dict:
                        raise Exception(f"The item number {index} of the 'environment' "
                                        "configuration is expected to contain a JSON object; "
                                        f"found a '{type(environment_variable)}' instead")

                    if not environment_variable["name"]:
                        raise Exception("The required field 'name' is missing from the item "
                                        f"number {index} of the 'environment_variable' "
                                        "configuration")

                    if not environment_variable["value"]:
                        raise Exception("The required field 'value' is missing from the item "
                                        f"number {index} of the 'environment_variable' "
                                        "configuration")

                    environment_variables.append(environment_variable)
            except Exception:
                logger.exception("An error occurred while parsing the 'environment' "
                                 "configuration")
                self.unit.status = BlockedStatus("Invalid 'environment' configuration")
                return

            for environment_variable in environment_variables:
                env_name = environment_variable["name"]
                env_template = environment_variable["value"]

                value = template_environment.from_string(env_template, template_globals).render()

                new_environment[env_name] = value

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

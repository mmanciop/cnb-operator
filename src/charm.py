#!/usr/bin/env python3
# Copyright 2021 Ubuntu
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

import functools
import logging
import json
import toml

from enum import Enum
from jinja2 import Environment

from jinja2.exceptions import UndefinedError

from os import path

from ops.charm import CharmBase
from ops.charm import ActionEvent, ConfigChangedEvent, PebbleReadyEvent, \
    RelationBrokenEvent, StartEvent, UpgradeCharmEvent
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import APIError

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


def _catch_block_status(func):

    @functools.wraps(func)
    def _decorator_func(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except BlockedStatusException as e:
            self.unit.status = BlockedStatus(e.message)
        except WaitingStatusException as e:
            self.unit.status = WaitingStatus(e.message)

    return _decorator_func


def _ensure_charm_state(func):
    """
        This decorator is meant to safeguard event handlers, and it
        ensures that the charm:

        1. Is not in a Blocked status
        2. All required consumed relations are available
        3. Pebble has been initialized and it has identified
           the OCI image as built with CNBs
    """

    @functools.wraps(func)
    def _decorator_func(self, *args, **kwargs):
        event = None
        if len(args) > 0:
            event = args[0]

        def _defer_event(event):
            if event is not None and type(event) != str:
                # TODO Quirk of the test harness?
                event.defer()

        # Check required relations are all there
        relations = self.model.relations
        missing_relations = [relation_name for relation_name
                             in relations
                             if not relations or not relations[relation_name]]

        if missing_relations:
            self.unit.status = BlockedStatus(
                "Required consumed relations are missing: "
                f"{', '.join(missing_relations)}"
            )

            logger.debug("Required consumed relations are missing: %s ; "
                         "deferring the event",
                         ", ".join(missing_relations))

            _defer_event(event)
            return

        # We also need to know that the application is a CNB one
        # (which implies that Pebble is ready and we snooped into
        # the application container to check that is looks like a
        # CNB image) before anything meaningful can happen
        if self._stored.application_type is None:
            # We might as well try, in some cases we do not get the
            # Pebble ready event
            self._determine_application_type()

            if self._stored.application_type is None:
                self.unit.status = MaintenanceStatus(
                    "Waiting for Pebble to initialize in the "
                    "application container"
                )

                logger.debug(
                    "Delaying event, application type not "
                    "determined yet"
                )

                _defer_event(event)

                return

        return func(self, *args, **kwargs)

    return _decorator_func


# TODOs:
#
# * Look up how to open ports in the pod
#
# * Watchdog the application, if it crashes silently
#   Pebble won't currently auto-restart it
#
# * Implement stop event to send soft term signal
#   via Pebble
#
class CloudNativeBuildpackCharm(CharmBase):
    """Charm applications packages with Cloud Native Buildpacks"""

    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)

        self.framework.observe(self.on.application_pebble_ready,
                               self._on_application_pebble_ready)

        self.framework.observe(self.on.config_changed,
                               self._on_config_changed)

        self.framework.observe(self.on.start,
                               self._on_start)
        self.framework.observe(self.on.upgrade_charm,
                               self._on_upgrade_charm)
        self.framework.observe(self.on.update_status,
                               self._on_update_status)

        for relation_name in self.meta.requires:
            self.framework.observe(self.on[relation_name].relation_joined,
                                   self._on_relation_upserted)
            self.framework.observe(self.on[relation_name].relation_changed,
                                   self._on_relation_upserted)
            self.framework.observe(self.on[relation_name].relation_broken,
                                   self._on_relation_broken)

        self.framework.observe(self.on.evaluate_template_action,
                               self._on_evaluate_template_action)
        self.framework.observe(self.on.dump_template_globals_action,
                               self._on_dump_template_globals_action)

        self.unit.status = MaintenanceStatus(
            "Waiting for Pebble to initialize in the application container"
        )

        self._stored.set_default(application_type=None)
        self._stored.set_default(current_environment={})
        # Key: path in application container; Value: hash of file content
        self._stored.set_default(rendered_files={})

    def _on_evaluate_template_action(self, event: ActionEvent):
        try:
            template = event.params["template"]

            logger.debug("Action 'template_action', template: %s", template)

            template_globals = self._calculate_template_globals()

            template_environment = Environment()

            rendered_template = template_environment \
                .from_string(template, template_globals).render()

            event.set_results({
                "template": template,
                "rendered-template": rendered_template
            })
        except Exception as e:
            logger.exception("Action 'evaluate-template' failed")
            event.fail(f"Action 'evaluate-template' failed: {str(e)}")

    def _on_dump_template_globals_action(self, event: ActionEvent):
        try:
            template_globals = self._calculate_template_globals()

            event.set_results({
                "template-globals": template_globals
            })
        except Exception as e:
            logger.exception("Action 'dump-template-globals' failed")
            event.fail(f"Action 'dump-template-globals' failed: {str(e)}")

    def _on_start(self, event: StartEvent):
        self._ensure_application_updated_and_running()

    @_catch_block_status
    def _on_upgrade_charm(self, event: UpgradeCharmEvent = None):
        # The logic to identify CNB images could have changed
        self._stored.application_type = None

        self._on_config_changed(None)
        self._on_application_pebble_ready(None)

    @_catch_block_status
    def _on_update_status(self, event=None):
        self._ensure_application_updated_and_running()

    @_catch_block_status
    def _on_config_changed(self, event: ConfigChangedEvent = None):
        self._ensure_application_updated_and_running()

    @_catch_block_status
    def _on_application_pebble_ready(self, event: PebbleReadyEvent = None):
        """Define and start the Cloud Native Buildpack lifecycle
           using the Pebble API.
        """

        self._determine_application_type()

        if self._stored.application_type == ApplicationType.NOT_CNB.name:
            raise BlockedStatusException(
                "Application not packaged with Cloud Native Buildpacks"
            )

        self._ensure_application_updated_and_running()

    def _determine_application_type(self):
        """ Check if it is a Buildpack application (by looking for the
            `${LAYERS_DIR}/config/metadata.toml` file) and,
            if found, which type of app it is
        """

        previous_type = self._stored.application_type

        if previous_type is not None:
            logger.debug("Checking if the application type changed from %s",
                         previous_type)

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
            else:
                logger.debug("An error occurred while looking in the "
                             "application container for the "
                             f"'{CNB_METADATA_PATH}' file; is Pebble ready?")
                return
        except Exception:
            logger.exception("An error occurred while looking in the application "
                             "container for the '%s' file", CNB_METADATA_PATH)

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

        if previous_type != self._stored.application_type:
            logger.info("Detected a %s application", self._stored.application_type)

    def _on_relation_upserted(self, event=None):
        self._ensure_application_updated_and_running()

    def _on_relation_broken(self, event: RelationBrokenEvent = None):
        self._ensure_application_updated_and_running()

    @_ensure_charm_state
    @_catch_block_status
    def _ensure_application_updated_and_running(self):
        """This is the most central part of the charm. This method must be invoked
           by pretty much every callback for every event that will require to either
           start or restart the application
        """

        application_container = self.unit.get_container("application")

        template_globals = self._calculate_template_globals()

        template_environment = Environment()
        new_environment = {}

        config = self._get_configs()

        if "environment" in config:
            for environment_variable in config["environment"]:
                env_name = environment_variable["name"]
                env_template = environment_variable["template"]

                try:
                    value = template_environment.from_string(env_template,
                                                             template_globals).render()

                    new_environment[env_name] = value
                except UndefinedError:
                    logger.exception(f"Cannot render environment variable '{env_name}'")
                    raise BlockedStatusException("Cannot render environment variables")

        if "files" in config:
            previous_files = self._stored.rendered_files

            for file in config["files"]:
                path = file["path"]
                content_template = file["template"]

                content = None
                try:
                    content = template_environment.from_string(content_template,
                                                               template_globals).render()
                except UndefinedError:
                    logger.exception(f"Cannot render file '{path}'")
                    raise BlockedStatusException("Cannot render files")

                content_hash = hash(content)
                if path in previous_files:
                    if content_hash is previous_files[path]:
                        continue

                self._stored.rendered_files[path] = content_hash

                try:
                    application_container.push(path, content)
                except Exception:
                    message = f"Cannot push file '{path}' to the application container"
                    logger.exception(message)
                    raise CannotPushFileToApplicationContainerException(path, message)

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

        self.unit.status = MaintenanceStatus("Evaluating an application (re)start")

        log_start = True
        if application_container.get_service("application").is_running():
            # TODO Check that files did not change
            if new_environment == self._stored.current_environment:
                logger.debug(
                    "No changes in configuration detected, the application "
                    "will not be restarted"
                )
            else:
                logger.info(
                    "Restarting the application to apply configuration "
                    "changes"
                )
                log_start = False

                self.unit.status = MaintenanceStatus("Restarting the application")

                application_container.stop("application")

        if not application_container.get_service("application").is_running():
            if log_start is True:
                self.unit.status = MaintenanceStatus("Starting the application")
                logger.info("Starting the application")

            logger.debug("Application environment: %s",
                         new_environment)

            application_container.start("application")
            logger.debug("Application started")

            self._stored.current_environment = new_environment
            logger.debug("Application environment updated to: %s", new_environment)

        self.unit.status = ActiveStatus()

    def _get_configs(self):
        with open(f"{path.dirname(path.realpath(__file__))}/config.json") as config_json:
            return json.load(config_json)

    def _calculate_template_globals(self):
        relations_data = {}
        for relation_name, relations in self.model.relations.items():
            relation_data = {
                "app": {}
            }

            relations_data[relation_name] = relation_data

            if len(relations) < 1:
                raise WaitingStatusException(
                    f"No remote unit is available for the {relation_name}"
                    " relation, cannot lookup application data")
            else:
                first_relation = relations[0]

                relation_data["app"] = first_relation.data[first_relation.app] or {}

                other_units = []
                for relation in relations:
                    for unit in relation.units:
                        if unit is not self.unit:
                            other_units.append({
                                field: relation.data[unit].get(field) for field in
                                relation.data[unit].keys()
                            })

                relation_data["units"] = other_units

        return {
            "relations": {
                "consumes": relations_data
            }
        }


class CannotPushFileToApplicationContainerException(Exception):

    def __init__(self, path, message):
        super().__init__(self)

        self.path = path
        self.message = message


class CannotDeleteFileFromApplicationContainerException(Exception):

    def __init__(self, path, message):
        super().__init__(self)

        self.path = path
        self.message = message


class BlockedStatusException(Exception):

    def __init__(self, message):
        super().__init__(self)

        self.message = message


class WaitingStatusException(Exception):

    def __init__(self, message):
        super().__init__(self)

        self.message = message


if __name__ == "__main__":
    main(CloudNativeBuildpackCharm)

#!/usr/bin/env python3
# Copyright 2021 Ubuntu
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

import functools
import logging
import json
import pathlib
import toml

from enum import Enum
from jinja2 import Environment
from jsonschema import validate

from jinja2.exceptions import UndefinedError

from ops.charm import CharmBase
from ops.charm import ActionEvent, ConfigChangedEvent, PebbleReadyEvent, \
    RelationBrokenEvent, StartEvent, UpgradeCharmEvent
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, \
    WaitingStatus
from ops.model import Application, Unit
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


def _load_json_schema(schema_filename):
    script_dir = pathlib.Path(__file__).parent.absolute()

    with open(f"{script_dir}/schema/{schema_filename}") as f:
        return json.load(f)


def _catch_block_status(func):

    @functools.wraps(func)
    def _decorator_func(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except BlockedStatusException as e:
            self.unit.status = BlockedStatus(e.message)

    return _decorator_func


def _ensure_charm_state(func):
    """
        This decorator is meant to safeguard event handlers, and it
        ensures that the charm:

        1. Is not in a Blocked status
        2. Its configuration has been parsed and is valid
        3. All required consumed relations are available
        4. Pebble has been initialized and it has identified
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

        # We need to have a parsed, validated configuration
        # before anything meaningful can happen
        if self._stored.parsed_configuration is None:
            if type(self.unit.status) == BlockedStatus:
                logger.debug("Delaying event, charm in blocked state")
            else:
                logger.debug("Delaying event, configuration not parsed yet")
                self.unit.status = WaitingStatus(
                    "Waiting to parse the configuration"
                )

            _defer_event(event)

            return

        # We is the charm is blocked, do NOTHING
        if type(self.unit.status) == BlockedStatus:
            logger.debug("Suppressing event handler, charm is blocked")
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
                self.unit.status = WaitingStatus(
                    "Waiting for Pebble to initialize in the application "
                    "container"
                )

                logger.debug(
                    "Delaying event, application type not determined yet"
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

    _environment_schema = _load_json_schema("environment.json")

    _files_schema = _load_json_schema("files.json")

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

        self.unit.status = WaitingStatus("Waiting to validate the configuration")

        # parsed and validated configuration
        self._stored.set_default(parsed_configuration=None)
        # is it Spring Boot, something else on JVM, or Node.js, etc.
        self._stored.set_default(application_type=None)
        # connection info for mongodb
        self._stored.set_default(current_environment={})

    def _parse_configuration(self, configuration_name, configuration_schema):
        try:
            if configuration_name in self.config:
                parsed_json = json.loads(self.config[configuration_name])
                validate(parsed_json, configuration_schema)
                return parsed_json
        except Exception as e:
            raise InvalidConfigurationException(configuration_name,
                                                str(e))

    def _on_evaluate_template_action(self, event: ActionEvent):
        try:
            template = event.params["template"]

            logger.debug("Action 'template_action', template: %s", template)

            template_globals = self._calculate_template_globals()

            logger.debug("Template globals: %s", template_globals)

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
        # The logic to identify CNB images could have changed, as well
        # as the schema for parsing configs
        self._stored.parsed_configuration = None
        self._stored.application_type = None

        self._on_config_changed(None)
        self._on_application_pebble_ready(None)

    @_catch_block_status
    def _on_config_changed(self, event: ConfigChangedEvent = None):
        self.unit.status = MaintenanceStatus("Validating the configuration")

        try:
            environment_json = self._parse_configuration(
                "environment",
                self._environment_schema
            )

            files_json = self._parse_configuration(
                "files",
                self._files_schema
            )

            self._stored.parsed_configuration = {
                "environment": environment_json,
                "files": files_json
            }
        except InvalidConfigurationException as e:
            logger.error(f"Invalid '{e.configuration_option}' configuration: "
                         f"{e.message}")
            raise BlockedStatusException(f"Invalid '{e.configuration_option}' "
                                         "configuration")

        self.unit.status = WaitingStatus("Waiting to start the application")

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

        logger.debug("Template globals: %s", template_globals)

        template_environment = Environment()
        new_environment = {}

        if "environment" in self._stored.parsed_configuration:
            environment = self._stored.parsed_configuration["environment"]

            for environment_variable in environment:
                env_name = environment_variable["name"]
                env_template = environment_variable["value"]

                try:
                    value = template_environment.from_string(env_template,
                                                             template_globals).render()

                    new_environment[env_name] = value
                except UndefinedError:
                    logger.exception(f"Cannot render environment variable '{env_name}'")
                    raise BlockedStatusException("Invalid 'environment' configuration")

        if "files" in self._stored.parsed_configuration:
            files = self._stored.parsed_configuration["files"]

            for file in files:
                path = file["path"]
                content_template = file["content"]

                content = None
                try:
                    content = template_environment.from_string(content_template,
                                                               template_globals).render()
                except UndefinedError:
                    logger.exception(f"Cannot render file '{path}'")
                    raise BlockedStatusException("Invalid 'files' configuration")

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

                self.unit.status = MaintenanceStatus("Restarting the application")

                application_container.stop("application")

        if not application_container.get_service("application").is_running():
            if log_start is True:
                self.unit.status = MaintenanceStatus("Starting the application")
                logger.info("Starting the application")

            logger.debug("Application environment based on configurations and relations: %s",
                         new_environment)

            application_container.start("application")
            logger.debug("Application started")

            self._stored.current_environment = new_environment
            logger.debug("Current environment updated to: %s", new_environment)

        self.unit.status = ActiveStatus()

    def _calculate_template_globals(self):
        relations_data = {}
        for relation_name, relation_metas in self.model.relations.items():
            relation_data = {
                "app": {}
            }

            if len(relation_metas) < 1:
                logger.debug("No remote unit is available, cannot lookup "
                             "application data for the '%s' relation",
                             relation_name)
            else:
                first_relation_meta = relation_metas[0]
                other_app = next(
                    filter(lambda item:
                           type(item) is Application and item.name != self.app.name,
                           first_relation_meta.data),
                    None)

                relation_data["app"] = first_relation_meta.data[other_app] or {}

                other_units_data = {}
                for relation_meta in relation_metas:
                    other_unit = next(
                        filter(lambda item:
                               type(item) is Unit and item.app.name != self.app.name,
                               relation_meta.data),
                        None)

                    other_units_data[other_unit.name] = relation_meta.data[other_unit]

                relation_data["units"] = other_units_data

                relations_data[relation_name] = relation_data

        return {
            "relations": {
                "consumed": relations_data
            }
        }


class CannotPushFileToApplicationContainerException(Exception):

    def __init__(self, path, message):
        super().__init__(self)

        self.path = path
        self.message = message


class InvalidConfigurationException(Exception):

    def __init__(self, configuration_option, message):
        super().__init__(self)

        self.configuration_option = configuration_option
        self.message = message


class BlockedStatusException(Exception):

    def __init__(self, message):
        super().__init__(self)

        self.message = message


if __name__ == "__main__":
    main(CloudNativeBuildpackCharm)

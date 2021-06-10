#!/bin/env python3

#
# Appcraft: Generate tailored bundles for your 1st party apps
#           packaged as Cloud Native Buildpacks
#

from jinja2 import Environment
from jsonschema import validate
from yaml.loader import SafeLoader

import argparse
import json
import logging
import os
import yaml

parser = argparse.ArgumentParser(description="Appcraft: Charm all them apps!")

parser.add_argument("application_name",
                    type=str,
                    help="the name of your application")

parser.add_argument("application_image",
                    type=str,
                    help="the OCI image name of your application")

parser.add_argument("manifest",
                    type=argparse.FileType("rb"),
                    help="path to the appcharm manifest, or `-` when piping from stdin")

parser.add_argument("-d", "--deploy",
                    action="store_true",
                    help="Deploy immediately the charm with Juju")

# TODO add a --dir flag

args = parser.parse_args()

application_name = args.application_name
application_image = args.application_image
manifest_content = yaml.load(args.manifest.read(), Loader=SafeLoader)
deploy_immediately = args.deploy or False

MANIFEST_SCHEMA = {
    "$schema": "http://json-schema.org/draft-06/schema#",
    "$ref": "#/definitions/Manifest",
    "definitions": {
        "Manifest": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "relations": {
                    "$ref": "#/definitions/Relations"
                },
                "environment": {
                    "type": "array",
                    "items": {
                        "$ref": "#/definitions/Environment"
                    }
                },
                "files": {
                    "type": "array",
                    "items": {
                        "$ref": "#/definitions/File"
                    }
                }
            },
            "required": [
                # "environment",
                # "files",
                "relations"
            ],
            "title": "Manifest"
        },
        "Environment": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {
                    "type": "string"
                },
                "value": {
                    "type": "string"
                }
            },
            "required": [
                "name",
                "value"
            ],
            "title": "Environment"
        },
        "File": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "path": {
                    "type": "string"
                },
                "content": {
                    "type": "string"
                }
            },
            "required": [
                "content",
                "path"
            ],
            "title": "File"
        },
        "Relations": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "consumes": {
                    "type": "array",
                    "items": {
                        "$ref": "#/definitions/Consumes"
                    }
                }
            },
            "required": [
                "consumes"
            ],
            "title": "Relations"
        },
        "Consumes": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "name": {
                    "type": "string"
                },
                "interface": {
                    "type": "string"
                }
            },
            "required": [
                "interface",
                "name"
            ],
            "title": "Consumed"
        }
    }
}

try:
    validate(manifest_content, MANIFEST_SCHEMA)
except Exception:
    logging.exception("Invalid manifest")
    os._exit(1)


required_relations = []
if "relations" in manifest_content and "consumes" in manifest_content["relations"]:
    for consumed_relation in manifest_content["relations"]["consumes"]:
        relation = {}

        relation["name"] = consumed_relation["name"]
        if "interface" in consumed_relation:
            relation["interface"] = consumed_relation["interface"]

        required_relations.append(relation)

template_globals = {
    "name": application_name,
    "requires": required_relations
}
# TODO Do description

metadata_template = """# Copyright 2021 Ubuntu
# See LICENSE file for licensing details.
name: {{name}}
description: |
  Sidecar charm to run OCI images built using Cloud Native Buildpacks.
  This charm will take the OCI image you bind as resource at deployment
  time, and check for the expected structure of OCI images built using
  Cloud Native Buildpacks (https://buildpacks.io), using the built-in
  lifecycle in a Pebble Layer.
summary: |
  Sidecar charm to run OCI images built using Cloud Native Buildpacks

containers:
  application:
    resource: application-image

requires:
{%- for relation in requires %}
  {{ relation.name }}:
    interface: {{ relation.interface }}
{%- endfor %}

resources:
  application-image:
    type: oci-image
    description: |
      OCI image containing the application to run.
      Must have been built with Cloud Native Buildpacks (https://buildpacks.io)

"""

template_environment = Environment()
metadata = template_environment.from_string(metadata_template, template_globals).render()

with open("metadata.yaml", "w") as metadata_yaml:
    metadata_yaml.write(metadata)

with open("src/config.json", "w") as environment_json:
    environment_json.write(json.dumps({
        "environment": manifest_content["environment"] or {},
        "files": manifest_content["files"] or []
    }))

print(f"Generating the {application_name} charm")

os.system("charmcraft pack 2> /dev/null")

print(f"{application_name}.charm has been generated.")

if deploy_immediately is True:
    # TODO send to stderr?
    print(f"Deploying the {application_name}.charm ...")
    os.system(f"juju deploy ./{application_name}.charm "
              f"--resource application-image={application_image}")
    # TODO Use juju deploy status code + {constant} as exit code
else:
    print(f""" To deploy it, run:

  $ juju deploy ./{application_name}.charm --resource application-image={application_image}
    """)

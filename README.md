# AppCraft and the Cloud Native Buildpack Meta-Operator

This charm and the accompanying `appcraft` utility allows you to wrap with Juju an existing OCI image built using [Cloud Native Buildpacks](https://buildpacks.io) and enables the administrator to use relation data to render configurations for the application.

## Usage

The user experience is centered over `appcraft`, which is a wrapper around `charmcraft` and, optionally, `juju deploy`, to customize the generic Cloud Native Buildpack charm found in the [cnb-charm](./cnb-charm) folder:

```sh
$ appcraft <manifest_path>
```

The `manifest_path` is the file path for a [manifest](#manifest) file, or `-` if the manifest is piped into `appcraft` over standard input.

## Lifecycle of the CNB charm

TODO document with a diagram under which circumstances the charm is blocked, and when the application is (re)started

## Manifest

The manifest allows you to parametrize the charm to be generated.
If contains the following configurations:

* `name`: name of the resulting charm; this value is passed verbatim to the `name` property of the charm's `metadata.yaml`
* `summary`: a short summary of the resulting charm; this value is passed verbatim to the `summary` property of the charm's `metadata.yaml`
* `description`: description of the resulting charm; this value is passed verbatim to the `description` property of the charm's `metadata.yaml`
* `consumes`: details which relations are consumed by this charm; this value is passed verbatim to the `consumes` property of the charm's `metadata.yaml`, but is also used to calculate the [template evaluation environment](#template-evaluation-environment) used to evaluate the `environment` and `files` configurations.
  At runtime, the charm will stay in status `Blocked` as long as one or more of the declared consumed relations are unfulfilled.
* `environment`: this configuration allows to specify environment variables that are set on the application at runtime.
  Each environment variable is specified as an object with two properties: `name` and `template`.
  The value of `name` is going to be used verbatim as the name of the environment variable; the value of the environment variable is specified via the `template` property, which contains a [Jinja 3](https://jinja.palletsprojects.com/en/3.0.x/) template that is evaluated at charm's runtime and that can access globals based on which relations are declared in the manifest, and which data bags are exposed by those relations at runtime.
  The Cloud Native Buildpack charm provides two [actions](#actions), `dump-template-globals` and `evaluate-template`, that are useful to understand which data is available to the templates.
* `files`: this configuration allows to specify files that are created in the application container at runtime, before the application is started.
  Each file is specified as an object with two properties: `path` and `template`.
  The value of `path` is going to be used as the absolute path of the file inside the container; the content of the file is specified via the `template` property, which contains a [Jinja 3](https://jinja.palletsprojects.com/en/3.0.x/) template that is evaluated at charm's runtime and that can access globals based on which relations are declared in the manifest, and which data bags are exposed by those relations at runtime.

The Cloud Native Buildpack charm provides two [actions](#actions), `dump-template-globals` and `evaluate-template`, that are useful to understand which data is available to the templates.

### Relations

Each relation added to a deployment of this charm will expose data to render environment variables and files in your application container.

#### Declaring consumed relations

The charm can optionally declare one or more relations it needs to have at runtime before the application is started.
For example, the following snippet of a `manifest.yaml` declares that the charm needs a relation, called `datasource`, using the `mongodb-datasource` interface:

```
name: cnb

requires:
  datasource: # Relation name, to be used in your templates to identify the relation
    interface: mongodb-datasource # Relation interfaces are codified in the charms that provide them
```

The `mongodb-datasource` interface is exposed by the [mongodb-k8s charm](https://charmhub.io/mongodb-k8s).
Therefore, in order for the `cnb` charm to be able to bootstrap the application inside its OCI image, a `mongodb-k8s` charm must be deployed inside the same Juju controller as the `cnb` charm, and a relation must be established between the `cnb` and `mongodb-k8s` charms.
The `cnb` charm will remain in status `Blocked` as long as the `datasource` relation is unfulfilled.
The same applies to cases when multiple required relations are declared: the charm stays in `Blocked` status as long as any of the required relations is unfulfilled.

### Templating environment variables and files

The `environment` and `files` properties of the [`manifest`](#manifest) allow to specify any number of environment and files to be made available to the application inside the OCI image, respectively.
The values of those environment variables, and the content of the files, is specified using _templates_.
The templates are [Jinja 3](https://jinja.palletsprojects.com/en/3.0.x/) templates, and they are evaluated at runtime by the charm using an [evaluation environment](#template-evaluation-environment) containing the data bags provided at runtime by the required relations.

#### A first example

Consider the following example:

```yaml
name: my_charm

relations:
  datasource: # No hyphen, no cry
    interface: mongodb

environment:
  - name: SPRING_DATA_MONGODB_URI
    value: '{{relations.consumes.datasource.app.replica_set_uri}}'
```

The manifest above declared one relation, called `datasource`, and uses the property `replica_set_uri` of the relation's _application bag_, accessed via the `.app` accessor.

*Note:* The prefix `relations.consumes.` must be appended to the templates, because in the future the charm may additionally expose other types of relations, e.g., _provides_ ones, as well as data that is not related to relations alone.

#### About hyphens

Avoid using hyphens in the relation name, as that makes it rather awkward consuming the data in templates.
For example, if we take the example above and rename the `datasource` relation to `data-source`, the template gets decidedly more awkward:

```yaml
name: my_charm

relations:
  data-source: # Mind the hyphen!
    interface: mongodb

environment:
  - name: SPRING_DATA_MONGODB_URI
    value: '{{relations.consumes["data-source"].app.replica_set_uri}}'
```

The same applies to relation properties.
If the `mongodb` relation exposed in the application data bag a `replica-set-uri` property, rather than `replica_set_uri`, the template would become:

```jinja
{{relations.consumes.datasource.app["replica-set-uri]}}
```

#### Templates and YAML strings

The YAML parser of `appcraft` will take offense if you write a manifest like the following:

```yaml
name: my_charm

relations:
  datasource:
    interface: mongodb

environment:
  - name: SPRING_DATA_MONGODB_URI
    value: {{relations.consumes.datasource.app.replica_set_uri}}
```

Note the lack of quotes of other ways of denoting literals as strings in YAML.
In this case, the parser thinks that the first `{` of the value opens an object, and immediately finding another `{` makes the YAML document invalid.
Instead, you can either quote the template, or use YAML multiline strings:

```yaml
name: my_charm

relations:
  datasource: # No hyphen, no cry!
    interface: mongodb

environment:
  - name: SPRING_DATA_MONGODB_URI
    value: |
      {{relations.consumes.datasource.app.replica_set_uri}}
```

Multiline strings are also very handy for templating non-trivial files, or using advanced Jinja capabilities like iterations and conditionals.

#### Template evaluation environment

The following globals will be accessible in all the templates for environment variables and files:

```json
{
  "relations": {
    "consumes": {
      <relation_name>: {
        "app": {
          <other_application_properties>
        },
        "units": [
          {
            <other_unit_properties>
          },
          ...
        ]
      }
    }, ...
  }
}
```

For example, if the application has a `mongodb` relation, which exposes only one unit, this is how the globals may look like:

```json
{
  "relations": {
    "consumes": {
      "mongodb": {
        "app": {
          "password": "3rtmQaj0VjCeDPP5",
          "provider_data": {
            "provides": {
              "mongodb": "4.4.1"
            },
            "ready": true
          },
          "replica_set_name": "rs0",
          "replica_set_uri": "mongodb://user-1:3rtmQaj0VjCeDPP5@mongodb-k8s-0.mongodb-k8s-endpoints:27017/admin",
          "replicated": true,
          "username": "user-1"
        },
        "units": [{
          # This unit exposes no data over the relation
        }]
      }
    }
  }
}
```

With such an evaluation environment, a charm built using the following manifest:

```yaml
name: my_charm

relations:
  datasource: # No hyphen, no cry!
    interface: mongodb

environment:
  - name: SPRING_DATA_MONGODB_URI
    value: |
      {{relations.consumes.datasource.app.replica_set_uri}}
```

will have the `SPRING_DATA_MONGODB_URI=mongodb://user-1:3rtmQaj0VjCeDPP5@mongodb-k8s-0.mongodb-k8s-endpoints:27017/admin` set on the application. 

## Actions

To access the current stastus of the template globals as seen by a particular unit, or try to evaluate a template without actually modifying the configuration of the charm, you can use the `dump-template-globals` and `evaluate-template` actions, respectively.

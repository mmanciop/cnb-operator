# Cloud Native Buildpack Operator

This charm is wraps with Juju an existing OCI image built using [Cloud Native Buildpacks](https://buildpacks.io) and enables the administrator to use relation data to render configurations for the application.

## Usage

```sh
$ juju deploy cnb-operator --resource application-image=<image> 
```

## Configurations

* `environment`: Stringified JSON array that allows you to specify how data coming through relations is exposed as environment variables to your application.
  For example, the following will use Jinja2 to template the value of two environment variables `SPRING_DATA_MONGODB_HOST` and `SPRING_DATA_MONGODB_PORT` based on the `hostname` and `port` properties of the consumed `mongodb` relation:

  ```json
  [
    {
      "name": "SPRING_DATA_MONGODB_URI",
      "value": "{{relations.consumed.mongodb.app.replicat_set_uri}}"
    }
  ]
  ```

  Refer to the [Relations](#relations) section for an overview of which relations are supported and what attributes they expose.

* `files`: Stringified JSON array that allows you to specify how data coming through relations is exposed as files in the container of your application.
  For example, the following will use Jinja2 to template the content of the `/my/mongodb/configuration` file using the `hostname` and `port` properties of the consumed `mongodb` relation:

  ```json
  [
    {
      "path": "/my/mongodb/replica_set_uri",
      "content": "{{relations.consumed.mongodb.app.replicat_set_uri}}"
    }
  ]
  ```

  Refer to the [Relations](#relations) section for an overview of which relations are supported and what attributes they expose.

## How does it work?

The charm accepts a resource as the OCI image to run, and inspects it for the common launcher across various Cloud Native Buildpack builders:

```
/cnb/launcher/web
```

When the launcher is found and is executable, the charm creates programmatically a layer for it and launches the application.

(The generality of the launcher has been verified with the [Paketo](https://paketo.io/) and [Google Buildpack](https://github.com/GoogleCloudPlatform/buildpacks) builders with both Spring Boot and Node.js apps.)

## Relations

Each relation added to a deployment of this charm will expose data to render environment variables and files in your application container.

### Templating based on relation data

The following globals will be accessible in the templates for environment variables and files:

```json
{
  "relations": {
    "consumed": {
      <relation_name>: {
        "app": {
          <other_application_properties>
        },
        "units": {
          <unit-name>: {
            <other_unit_properties>
          }
        }
      }
    }, ...
  }
}
```

For example, if the application has a `mongodb` relation with one unit, this is how the globals may look like:

```json
{
  "relations": {
    "consumed": {
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
        "units": {
          "mongodb-k8s/0": {}
        }
      }
    }
  }
}
```

So, to access the `replica_set_uri`, you can use the following expression:

```
{{relations.consumed.mongodb.app.replica_set_uri}}
```

Instead, to send traces to a Jaeger endpoint, this `environment` configuration sets the necessary `JAEGER_AGENT_HOST` and `JAEGER_AGENT_PORT` environment variables:

```sh
$ juju config <app_name> environment='[{"name":"JAEGER_AGENT_HOST","value":"{{relations.consumed.jaeger.units[\"jaeger/0\"][\"agent-address\"]}}"},{"name":"JAEGER_AGENT_PORT","value":"{{relations.consumed.jaeger.units[\"jaeger/0\"].port}}"}]'
```

To access the current stastus of the template globals as seen by a particular unit, or try to evaluate a template without actually modifying the configuration of the charm, you can use the `dump-template-globals` and `evaluate-template` actions, respectively.

### Supported relations

The relations currently supported are:

| Relation interface | Charm |
| --- | --- |
| `mongodb` | [MongoDB K8S](https://charmhub.io/mongodb-k8s) |
| `jaeger` | Pending |
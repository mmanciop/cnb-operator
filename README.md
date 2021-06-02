# Cloud Native Buildpack Operator

## Description

The goal of this charm is to wrap with Juju an existing OCI image built using [Cloud Native Buildpacks](https://buildpacks.io) and, for Spring Boot applications, automatically create autoconfiguration for Spring Data and other Spring facilities to consume Juju-provide relations.

## How does it work?

The charm accepts a resource as the OCI image to run, and inspects it for the common launcher across various Cloud Native Buildpack builders:

```
/cnb/launcher/web
```

When the launcher is found and is executable, the charm creates programmatically a layer for it and launches the application.

(The generality of the launcher has been verified with the [Paketo](https://paketo.io/) and [Google Buildpack](https://github.com/GoogleCloudPlatform/buildpacks) builders with both Spring Boot and Node.js apps.)

## Supported integrations

* [Spring Data MongoDB](https://spring.io/projects/spring-data-mongodb): if you add a `mongodb` relation between a deployment of this charm and a deployment of the [MongoDB K8S](https://charmhub.io/mongodb-k8s) charm, the `SPRING_DATA_MONGODB_HOST` and `SPRING_DATA_MONGODB_PORT` environment variables will be automatically set for your application. **Note:** You will need to specify the MongoDB database yourself (e.g., via the `SPRING_DATA_MONGODB_HOST` environment variable), as the `mongodb` relation does not carry that piece of information. [^1]

[^1] Truly, `juju add relation --config` cannot be here a day too soon :-)
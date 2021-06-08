# Development

## Setup

Create and activate a virtualenv with the development requirements:

```
virtualenv -p python3 venv
source venv/bin/activate
pip install -r cnb-charm/requirements-dev.txt
```

## Routine

### Testing

The Python operator framework includes a very nice harness for testing
operator behaviour without full deployment. Just `run_tests`:

```
( cd cnb-charm ; ./run_tests )
```

### Deploy test app

Ensure that `microk8s` has the registry enabled:

```sh
$ microk8s enable registry
```

Build a Spring Boot test app and push it to the `microk8s` registry:

```sh
$ ( cd test/apps/java/spring-data-mongodb-reactive/; ./mvnw spring-boot:build-image -Dspring-boot.build-image.imageName=localhost:32000/test-app )
$ docker push localhost:32000/test-app
```

### Build the charm

```sh
$ (cd cnb-charm; charmcraft pack)
```

### Deploy charm

```sh
$ juju deploy ./cnb-charm/cnb.charm --resource application-image=localhost:32000/test-app
```

### Update charm

From the project root:

```sh
$ juju refresh cnb --path ./cnb-charm/cnb.charm
$ juju debug-log
```

## Recurring tasks

### Add support for a new relation

1. List the relation `name` and `interface` under `requires` in [metadata.yaml](./cnb-charm/metadata.yaml)
2. Update the [Supported relations](./README.md#supported-relations) section of `README.md`

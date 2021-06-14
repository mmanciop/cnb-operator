# Development

## Developing

Create and activate a virtualenv with the development requirements:

```
virtualenv -p python3 venv
source venv/bin/activate
pip install -r requirements-dev.txt
```

## Unit Testing

The Python operator framework includes a very nice harness for testing
operator behaviour without full deployment. Just `run_tests`:

```
./run_tests
```

## Integration Tests

### Setup

Ensure that `microk8s` has the registry enabled:

```sh
$ microk8s enable registry
```

### Build Test App

Build a Spring Boot test app and push it to the `microk8s` registry:

```sh
$ ( cd tests/apps/java/spring-data-mongodb-reactive/; ./mvnw spring-boot:build-image -Dspring-boot.build-image.imageName=localhost:32000/test-app )
$ docker push localhost:32000/test-app
```

### Generate the charm

Save the following YAML document to a `manifest.yaml` file:

```yaml
---
name: cnb

requires:
  database:
    interface: mongodb

environment:
- name: SPRING_DATA_MONGODB_URI
  template: '{{relations.consumes.database.app.replica_set_uri}}'

```

Generate the `cnb` charm:

```sh
$ ./appcraft manifest.yaml
```

### Deploy the charm

```sh
$ juju deploy ./cnb.charm --resource application-image=localhost:32000/test-app
```

### Update the charm

From the project root:

```sh
$ ./appcraft manifest.yaml
$ juju refresh cnb --path ./cnb.charm
$ juju debug-log
```

# Test App

## Paketo Builder

### Setup

1. Install the [`pack`](https://buildpacks.io/docs/tools/pack/) utility.
2. Ensure you have a Docker deamon up and running and that your shell can access the Docker socket (e.g., `docker ps` should work).

### Build

```sh
pack build test-app --builder paketobuildpacks/builder:base
```

## Spring Boot Maven Plugin

### Setup

Ensure you have a Docker deamon up and running and that your shell can access the Docker socket (e.g., `docker ps` should work).

### Build

```sh
./mvnw spring-boot:build-image -Dspring-boot.build-image.imageName=test-app
```

## Issues

```
Caused by: com.github.dockerjava.api.exception.NotFoundException: Status 404: {"message":"No such image: testcontainers/ryuk:0.3.0"}
```

Manually pull into your local docker registry the `testcontainers` and `mongo` images:

```sh
docker pull testcontainers/ryuk:0.3.0
docker pull mongo:4.4.6
```
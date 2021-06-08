package com.canonical.java.test;

import java.util.Arrays;

import com.canonical.java.test.mongodb.Thing;
import com.canonical.java.test.mongodb.ThingRepository;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.util.TestPropertyValues;
import org.springframework.context.ApplicationContextInitializer;
import org.springframework.context.ConfigurableApplicationContext;
import org.springframework.test.context.ContextConfiguration;
import org.springframework.test.web.reactive.server.WebTestClient;
import org.testcontainers.containers.MongoDBContainer;
import org.testcontainers.junit.jupiter.Testcontainers;

import reactor.core.scheduler.Schedulers;
import reactor.test.StepVerifier;

@ContextConfiguration(initializers = {SpringWebfluxMongoDBReactiveApplicationTests.Initializer.class})
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
@Testcontainers
class SpringWebfluxMongoDBReactiveApplicationTests {

	private static final MongoDBContainer MONGODB_CONTAINER = new MongoDBContainer("mongo:4.4.6");

	static {
		MONGODB_CONTAINER.start();
	}

	@Autowired
	private WebTestClient client;

	@Autowired
	private ThingRepository thingRepository;

	@Test
	void getAllTheThings() {
		final Thing t1 = new Thing("Thing #1");
		final Thing t2 = new Thing("Thing #2");

		thingRepository.saveAll(Arrays.asList(t1, t2))
			.subscribeOn(Schedulers.immediate())
			.subscribe();

		StepVerifier.create(client
			.get()
			.uri("/api/things")
			.exchange()
			.returnResult(Thing.class)
			.getResponseBody()
		)
		.expectNext(t1)
		.expectNext(t2)
		.expectComplete()
		.verify();
	}

	static class Initializer implements ApplicationContextInitializer<ConfigurableApplicationContext> {

		public void initialize(ConfigurableApplicationContext configurableApplicationContext) {
			TestPropertyValues.of(
				"spring.data.mongodb.uri=" + MONGODB_CONTAINER.getReplicaSetUrl()
		  	).applyTo(configurableApplicationContext.getEnvironment());
	  	}

	}

}

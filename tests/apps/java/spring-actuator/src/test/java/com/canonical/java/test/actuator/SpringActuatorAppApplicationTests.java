package com.canonical.java.test.actuator;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.test.web.reactive.server.WebTestClient;

@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class SpringActuatorAppApplicationTests {

	@Autowired
	private WebTestClient client;

	@Test
	void getGreetings() {
		client
			.get()
			.uri("/api/greetings")
			.exchange()
			.expectBody(String.class)
			.isEqualTo("Hello World!");
	}

	@Test
	void getActuatorHealth() {
		client
			.get()
			.uri("/actuator/health")
			.exchange()
			.expectBody()
			.jsonPath(".status")
			.isEqualTo("UP");
	}

}

package com.canonical.java.test.actuator;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import reactor.core.publisher.Mono;

@SpringBootApplication
public class SpringActuatorAppApplication {

	public static void main(String[] args) {
		SpringApplication.run(SpringActuatorAppApplication.class, args);
	}

	@RestController
	@RequestMapping(path = "/api")
	static class Api {

		@GetMapping(path = "/greetings")
		public Mono<String> getAllTheThings() {
			return Mono.just("Hello World!");
		}

	}
}

package com.canonical.java.test;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import reactor.core.publisher.Flux;

@SpringBootApplication()
public class SpringWebfluxJaegerApplication {

	public static void main(String[] args) {
		SpringApplication.run(SpringWebfluxJaegerApplication.class, args);
	}

	@RestController
	@RequestMapping(path = "/api")
	static class Api {

		@GetMapping(path = "/things")
		public Flux<String> getAllTheThings() {
			return Flux.just("Hello", "World");
		}

	}

}

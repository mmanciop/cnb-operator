package com.canonical.java.test;

import com.canonical.java.test.mongodb.Thing;
import com.canonical.java.test.mongodb.ThingRepository;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import reactor.core.publisher.Flux;

@SpringBootApplication()
public class SpringWebfluxMongoDBReactiveApplication {

	public static void main(String[] args) {
		SpringApplication.run(SpringWebfluxMongoDBReactiveApplication.class, args);
	}

	@RestController
	@RequestMapping(path = "/api")
	static class Api {

		private final ThingRepository thingRepository;

		Api(ThingRepository thingRepository) {
			this.thingRepository = thingRepository;
		}

		@GetMapping(path = "/things")
		public Flux<Thing> getAllTheThings() {
			return thingRepository.findAll();
		}

	}

}

package com.canonical.java.test.mongodb;

import org.springframework.data.mongodb.repository.ReactiveMongoRepository;

public interface ThingRepository extends ReactiveMongoRepository<Thing, String> {

}
package com.example.sample;

import java.util.List;

interface Identifiable {
    String getId();
}

enum Status {
    ACTIVE, INACTIVE
}

class BaseEntity {
    protected String name;
    public BaseEntity(String name) {
        this.name = name;
    }
}

public class User extends BaseEntity implements Identifiable {
    private String id;
    private Status status = Status.ACTIVE;

    public User(String id, String name) {
        super(name);
        this.id = id;
    }

    @Override
    public String getId() {
        return this.id;
    }

    public String process() {
        return helperCall(this.name);
    }

    private String helperCall(String input) {
        return "processed_" + input;
    }
}

import 'dart:async';

abstract class Identifiable {
    String get id;
}

enum Status {
    active,
    inactive
}

class BaseEntity {
    final String name;
    BaseEntity(this.name);
}

class User extends BaseEntity implements Identifiable {
    @override
    final String id;
    Status status = Status.active;

    User(this.id, String name) : super(name);

    String process() {
        return helperCall(name);
    }

    String helperCall(String input) {
        return 'processed_$input';
    }
}

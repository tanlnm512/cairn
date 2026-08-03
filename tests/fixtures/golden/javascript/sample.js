import { EventEmitter } from 'events';

const Status = {
    ACTIVE: 'active',
    INACTIVE: 'inactive'
};

class BaseEntity {
    constructor(name) {
        this.name = name;
    }
}

class User extends BaseEntity {
    constructor(id, name) {
        super(name);
        this.id = id;
        this.status = Status.ACTIVE;
    }

    process() {
        return this.helperCall(this.name);
    }

    helperCall(input) {
        return `processed_${input}`;
    }
}

export { Status, BaseEntity, User };

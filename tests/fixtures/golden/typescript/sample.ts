import { EventEmitter } from 'events';

export interface Identifiable {
    id: string;
}

export enum Status {
    ACTIVE = 'active',
    INACTIVE = 'inactive'
}

export class BaseEntity {
    constructor(public name: string) {}
}

export class User extends BaseEntity implements Identifiable {
    public status: Status = Status.ACTIVE;

    constructor(public id: string, name: string) {
        super(name);
    }

    public process(): string {
        return this.helperCall(this.name);
    }

    private helperCall(input: string): string {
        return `processed_${input}`;
    }
}

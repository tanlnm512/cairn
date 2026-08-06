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

// Decorators: class-level decorator must attach to the class, method-level
// to the method. Tests both bare `class` and `export class` shapes (the
// decorator's tree-sitter parent differs: class_declaration vs export_statement).
@Controller('/api')
class UserController {
    @Get(':id')
    getOne() {}
}

@Injectable()
export class AuthService {
    @Post('/login')
    login() {}
}

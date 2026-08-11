<?php
namespace App\Models;

require_once __DIR__ . "/helpers.php";
use App\Services\Logger;
use App\Utils\{Formatter, Validator};

interface Greeter {
    public function greet(): string;
}

trait NameAware {
    protected string $name;

    public function getName(): string {
        return $this->name;
    }
}

enum Suit: string {
    case Hearts = "hearts";
    case Spades = "spades";

    public function color(): string {
        return "red";
    }
}

class User implements Greeter {
    use NameAware;

    public string $role;
    public string $status;

    public function __construct(public string $name, private int $id) {
        $this->role = "member";
    }

    public function greet(): string {
        $logger = new Logger();
        $logger?->info("greet called");
        return "Hello " . $this->getName();
    }
}

function greet_all(array $users): void {
    foreach ($users as $u) {
        echo $u->greet();
    }
}

greet_all([]);

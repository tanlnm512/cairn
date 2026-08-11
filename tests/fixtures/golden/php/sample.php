<?php
namespace App\Models;

require_once __DIR__ . "/helpers.php";
use App\Services\Logger;

interface Greeter {
    public function greet(): string;
}

trait NameAware {
    protected string $name;

    public function getName(): string {
        return $this->name;
    }
}

class User implements Greeter {
    use NameAware;

    public function __construct(string $name) {
        $this->name = $name;
    }

    public function greet(): string {
        $logger = new Logger();
        $logger->info("greet called");
        return "Hello " . $this->getName();
    }
}

function greet_all(array $users): void {
    foreach ($users as $u) {
        echo $u->greet();
    }
}

greet_all([]);

use std::collections::HashMap;

pub fn main() {
    let mut m = HashMap::new();
    helper(2);
}

fn helper(x: i64) -> i64 {
    x * 2
}

pub struct Config {
    pub name: String,
}

impl Config {
    pub fn new(name: String) -> Self {
        Config { name }
    }
}

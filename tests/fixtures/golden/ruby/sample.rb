require "logger"
require_relative "./helpers"

module App
  class User < Base
    attr_accessor :name

    def initialize(name)
      @name = name
    end

    def greet
      logger = Logger.new
      logger.info("greet called")
      "Hello " + @name
    end

    def self.find(id)
      User.new(id)
    end
  end
end

def greet_all(users)
  users.each { |u| puts u.greet }
end

greet_all([])

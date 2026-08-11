using System;
using System.Collections.Generic;
using App.Services;

namespace App.Models
{
    public interface IGreeter
    {
        string Greet();
    }

    public abstract class BaseEntity
    {
        public int Id { get; set; }
    }

    public class User : BaseEntity, IGreeter
    {
        private readonly ILogger _logger;

        public User(ILogger logger)
        {
            _logger = logger;
        }

        public string Greet()
        {
            _logger.Info("greet called");
            return GetName();
        }

        private string GetName() => "user";

        public static User Find(int id)
        {
            return new User(null);
        }
    }

    public enum Status
    {
        Active,
        Inactive
    }
}

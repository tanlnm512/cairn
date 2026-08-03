package com.example.sample

import java.util.List

interface Identifiable {
    val id: String
}

enum class Status {
    ACTIVE, INACTIVE
}

open class BaseEntity(open val name: String)

class User(override val id: String, override val name: String) : BaseEntity(name), Identifiable {
    var status: Status = Status.ACTIVE

    fun process(): String {
        return helperCall(name)
    }

    private fun helperCall(input: String): String {
        return "processed_$input"
    }
}

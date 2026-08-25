// when-entry guard conditions; canonical shapes: fwcd/tree-sitter-kotlin test/corpus/expressions.txt

package fixtures.kotlin.modern

sealed interface Animal {
    val hungry: Boolean
}

class Dog(override val hungry: Boolean) : Animal
class Cat(override val hungry: Boolean, val mouseHunter: Boolean) : Animal

class Feeder {
    fun feed(animal: Animal) {
        when (animal) {
            is Dog if animal.hungry -> feedDog()
            else -> throwToy()
        }
        when (animal) {
            !is Cat if animal.hungry -> feedAnother()
            else -> throwToy()
        }
        when (animal) {
            is Cat if !animal.mouseHunter && animal.hungry -> feedCat()
            is Dog -> petDog()
            else -> ignore()
        }
    }

    fun classify(n: Int) {
        when (n) {
            in 1..10 if n > 5 -> println("big")
            else -> println("small")
        }
        when (n) {
            0 if isZero() -> doSomething()
            else -> doDefault()
        }
    }
}

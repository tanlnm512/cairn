// KEEP-0438 name-based and positional destructuring; canonical shapes: fwcd/tree-sitter-kotlin test/corpus/destructuring.txt

package fixtures.kotlin.modern

class PairKeeper(private val pair: Pair<Int, String>, private val obj: Config) {

    fun fullForm() {
        (val a, val b) = pair
        println(a)
    }

    fun fullFormWithTypes() {
        (val a: Int, var b: String) = pair
        println(b)
    }

    fun fullFormWithRenaming() {
        (val localA = propA, val localB = propB) = obj
    }

    fun fullFormWithTypeAndRenaming() {
        (val a: String = propA, val b) = obj
    }

    fun shortForms() {
        val (a, b) = pair
        val (c: Int, d: String) = pair
        val (e = propA, f) = obj
    }

    fun positionalForms() {
        val [a, b] = pair
        val [c: Int, d: String] = pair
    }

    fun inLoopAndLambda(map: Map<String, Int>) {
        for ((k, v) in map) {
            println(k)
        }
        map.forEach { (k, v) -> println(v) }
    }
}

class Config(val propA: String, val propB: String)

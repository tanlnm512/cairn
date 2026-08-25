// trailing commas in declarations, type lists, calls, indexing, lambdas, and destructuring;
// canonical shapes: fwcd/tree-sitter-kotlin test/corpus/{classes,functions,types,expressions,destructuring}.txt

package fixtures.kotlin.modern

data class Configuration(
    val audience: String,
    val realm: String,
)

typealias Lookup<E, Q,> = Map<E, Q,>

typealias Processor = (Int, String,) -> Unit

class Runner(private val pair: Pair<Int, String>) {
    fun sum(a: Int, b: Int,): Int {
        return a + b
    }

    fun call(args: List<Int>) {
        take(args[0], args[1],)
    }

    fun index(matrix: Array<IntArray>) {
        val cell = matrix[0, 1,]
    }

    fun lambda(ints: List<Int>): List<Int> {
        return ints.map { x, -> x * 2 }
    }

    fun destructure(single: IntWrapper) {
        val (a, b,) = pair
        val [c, d,] = pair
        val (e,) = single
    }

    private fun take(a: Int, b: Int) {}
}

class IntWrapper(val value: Int)

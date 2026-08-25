// multi-dollar string interpolation; canonical shapes: fwcd/tree-sitter-kotlin test/corpus/literals.txt

package fixtures.kotlin.modern

class Templates(private val name: String) {
    val greeting = $$"hello $$name"

    val multiline = $$"""
        In here we can have a verbatim $, while interpolation is triggered
        by $$name
        """

    val excessDollars = $$"""$$$name"""

    val tripleDollar = $$$"""triple $$$name"""

    val literalDollar = $$"literal $ and $$name"

    fun expressionInterpolation(x: Int): String {
        return $$"value: $${x + 1}"
    }

    fun multiple(x: Int, y: Int): String {
        return $$"$$x and $${y + x}"
    }
}

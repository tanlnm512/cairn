# Tree-sitter Query Capture Contract (.scm)

All `.scm` language queries must conform strictly to this standardized capture name vocabulary:

## Declarations & Symbols
* `@symbol.class` - Class declaration
* `@symbol.interface` - Interface or Protocol declaration
* `@symbol.enum` - Enum declaration
* `@symbol.function` - Top-level function definition
* `@symbol.method` - Class or interface method definition
* `@symbol.property` - Class field, property, or attribute
* `@symbol.variable` - Variable declaration

## Symbol Details
* `@name` - Identifier / symbol name
* `@doc` - Docstring or documentation comment
* `@signature` - Declaration header / signature span
* `@param.name` - Parameter name
* `@param.type` - Parameter type annotation
* `@return_type` - Function/method return type annotation
* `@modifier` - Modifiers (e.g. `public`, `override`, `async`, `static`)

## Edges
* `@edge.call.target` - Function or method call invocation target
* `@edge.extends.target` - Base class inheritance target
* `@edge.implements.target` - Interface implementation target

## Imports
* `@import.path` - Imported package, module, or symbol path

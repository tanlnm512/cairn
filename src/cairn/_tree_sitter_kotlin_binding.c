/* Python binding for the vendored fwcd Kotlin grammar. Exposes exactly one
 * function, language() -> PyCapsule named "tree_sitter.Language" -- the
 * capsule name the tree_sitter runtime verifies in Language(). */

#ifndef PY_SSIZE_T_CLEAN
#define PY_SSIZE_T_CLEAN
#endif

#include <Python.h>

typedef struct TSLanguage TSLanguage;

const TSLanguage *tree_sitter_kotlin(void);

static PyObject *language(PyObject *Py_UNUSED(self), PyObject *Py_UNUSED(args)) {
    return PyCapsule_New((void *)tree_sitter_kotlin(), "tree_sitter.Language", NULL);
}

static PyMethodDef methods[] = {
    {"language", language, METH_NOARGS, "Get the tree-sitter language for this grammar."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef module = {
    PyModuleDef_HEAD_INIT,
    .m_name = "cairn._tree_sitter_kotlin",
    .m_size = -1,
    .m_methods = methods,
};

PyMODINIT_FUNC PyInit__tree_sitter_kotlin(void) {
    return PyModule_Create(&module);
}

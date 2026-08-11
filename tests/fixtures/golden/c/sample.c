#include <stdio.h>
#include <string.h>
#include "utils.h"

typedef struct Point {
    int x;
    int y;
} Point;

int add(int a, int b) {
    return a + b;
}

static void greet(struct Point *p) {
    printf("hi %d", p->x);
    add(1, 2);
}

Point *origin(void) {
    Point *p = malloc(sizeof(Point));
    p->x = 0;
    return p;
}

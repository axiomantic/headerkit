#ifndef FASTMATH_API_H
#define FASTMATH_API_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Forward opaque handle for Nim object */
typedef struct FastMatrix FastMatrix;

/* Nim runtime lifecycle */
void NimMain(void);
void NimDestroyGlobals(void);

/* Exported procs */
FastMatrix* createMatrix(int64_t rows, int64_t cols);
void destroyMatrix(FastMatrix* m);
int64_t addNumbers(int64_t a, int64_t b);
double computeSum(FastMatrix* m);

#ifdef __cplusplus
}
#endif

#endif /* FASTMATH_API_H */

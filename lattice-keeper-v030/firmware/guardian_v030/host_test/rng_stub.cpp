// Host-only stub: verification never draws random numbers.
#include <RNG.h>
#include <cstdlib>
RNGClass::RNGClass() {}
RNGClass::~RNGClass() {}
void RNGClass::rand(uint8_t *, size_t) { std::abort(); }
RNGClass RNG;

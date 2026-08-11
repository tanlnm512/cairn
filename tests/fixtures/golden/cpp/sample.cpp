#include <vector>
#include <memory>
#include "engine.h"

namespace app {

class Engine {
public:
    Engine(int power) : power_(power) {}
    virtual ~Engine() {}
    int getPower() const { return power_; }
    virtual void start() = 0;
private:
    int power_;
};

class V8 : public Engine {
public:
    void start() override {
        launch(100);
    }
};

template<typename T>
T max_val(T a, T b) {
    return a > b ? a : b;
}

void run(Engine *e) {
    e->getPower();
    max_val<int>(1, 2);
}

}  // namespace app

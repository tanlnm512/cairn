"""Alpha side of the multirepo workspace."""


def alpha_provider():
    return "alpha"


def alpha_consumer():
    left = alpha_provider()
    return left + beta_util()

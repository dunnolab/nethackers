import time


class Bot:
    def reset(self, initial_observation):
        del initial_observation

    def act(self, observation):
        del observation
        time.sleep(10)
        return 0


def make_agent():
    return Bot()

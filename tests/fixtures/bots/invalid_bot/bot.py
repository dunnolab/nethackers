class Bot:
    def reset(self, initial_observation):
        del initial_observation

    def act(self, observation):
        del observation
        return True


def make_agent():
    return Bot()

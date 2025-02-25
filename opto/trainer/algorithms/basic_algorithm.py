
import numpy as np
from opto import trace
from opto.optimizers import OptoPrime
from opto.trainer.algorithms.algorithm import BaseAlgorithm
from opto.trainer.loader import DataLoader

@trace.bundle()
def concat_list_as_str(*items):
    """ Concatenate the items into a single string """
    output = ''
    for i, item in enumerate(items):
        output += f'ID {[i]}: {item}\n'
    return output

class MinibatchUpdate(BaseAlgorithm):

    """ Minibatched optimization algorithm.

        The computed output of each instance in the minibatch is aggregated and a batched feedback is provided to update the agent.
    """

    def __init__(self,
                agent,
                optimizer,
                logger = None,
                *args,
                **kwargs,
                ):
        super().__init__(agent)
        self.optimizer = optimizer
        # The logger needs to provide `log(name, data, step, **kwargs)`` method to log the training process.
        self.logger = logger
        self.n_iters = 0 # number of iterations

    def evaluate(self, agent, teacher, xs, infos, min_score=None):
        """ Evaluate the agent on the given dataset. """
        test_scores = super().evaluate(agent, teacher, xs, infos, min_score=min_score)
        if test_scores is not None:
            self.logger.log('Average test score', np.mean(test_scores), self.n_iters, 'green')
        return test_scores

    def train(self,
              teacher,
              train_dataset,
              *,
              num_epochs = 1,  # number of training epochs
              batch_size = 1,  # batch size for updating the agent
              test_dataset = None, # dataset of (x, info) pairs to evaluate the agent
              eval_frequency = 1, # frequency of evaluation
              log_frequency = None,  # frequency of logging
              min_score = None,  # minimum score to update the agent
              verbose = False,  # whether to print the output of the agent
              ):
        """
                Given a dataset of (x, info) pairs, the algorithm will:
                1. Forward the agent on the inputs and compute the feedback using the teacher.
                2. Update the agent using the feedback.
                4. Evaluate the agent on the test dataset and log the results.
                5. Stop training if the score is above the threshold.
        """

        log_frequency = log_frequency or eval_frequency # frequency of logging (default to eval_frequency)

        # Evaluate the agent before learning
        if eval_frequency>0:
            self.evaluate(self.agent, teacher, test_dataset['inputs'], test_dataset['infos'], min_score=min_score) # and log

        loader = DataLoader(train_dataset, batch_size=batch_size)
        train_scores = []
        for i in range(num_epochs):

            # Train agent
            for xs, infos in loader:

                # Forward and compute feedback for each instance in the minibatch
                targets, feedbacks, scores = [], [], []
                for x, info in zip(xs, infos):  # # TODO async forward
                    target, score, feedback = self.step(self.agent, x, teacher, info)
                    scores.append(score)
                    targets.append(target)
                    feedbacks.append(feedback)
                    train_scores.append(score)  # persist across iterations for logging

                # Concatenate the targets and feedbacks into a single string
                target = concat_list_as_str(*targets)
                feedback = concat_list_as_str(*feedbacks).data  # str

                # Update the agent
                self.update(target, feedback, verbose=verbose)
                self.n_iters += 1

                # Evaluate the agent after update
                if test_dataset is not None and self.n_iters % eval_frequency == 0:
                    self.evaluate(self.agent, teacher, test_dataset['inputs'], test_dataset['infos'], min_score=min_score) # and log

                # Logging
                if self.n_iters % log_frequency == 0:
                    print(f"Epoch: {i}. Iteration: {self.n_iters}")
                    self.logger.log("Average train score", np.mean(train_scores), self.n_iters)
                    for p in self.agent.parameters():
                        self.logger.log(f"Parameter: {p.name}", p.data, self.n_iters, 'red')


    def update(self, target, feedback, verbose=False):
        """ Subclasses can implement this method to update the agent. """
        self.optimizer.zero_feedback()
        self.optimizer.backward(target, feedback)
        self.optimizer.step(verbose=verbose)

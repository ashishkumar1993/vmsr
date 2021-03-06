import os
import torch
import torch.nn as nn
import torch.nn.functional as F

from distributions_c import Categorical, DiagGaussian
#from utils import init, init_normc_
from pytorch_code.model_utils import Conditional_Net, ActionConditionalLSTM, Conditional_Net_RN5N
import numpy as np

def init(module, weight_init, bias_init, gain=1):
    weight_init(module.weight.data, gain=gain)
    bias_init(module.bias.data)
    return module

class Flatten(nn.Module):
    def forward(self, x):
        return x.view(x.size(0), -1)


class Policy(nn.Module):
    def __init__(self, obs_shape, action_space, base_kwargs=None):
        super(Policy, self).__init__()
        if base_kwargs is None:
            base_kwargs = {}
        
        self.random_act = False
        if base_kwargs['policy_type'] == 'Nav':
            self.base = NavBase(obs_shape, **base_kwargs)
        elif base_kwargs['policy_type'] == 'NavRand':
            base_kwargs['policy_type'] = 'Nav'
            self.base = NavBase(obs_shape, **base_kwargs)
            self.random_act = True
            self.rng_act = np.random.RandomState(0)
        elif base_kwargs['policy_type'] == 'HRL':
            self.base = HRLBase(obs_shape, **base_kwargs)
        elif base_kwargs['policy_type'] == 'Operator':
            self.base = OperatorBase(obs_shape, **base_kwargs)

        # elif len(obs_shape) == 3:
        #     self.base = CNNBase(obs_shape[0], **base_kwargs)
        # elif len(obs_shape) == 1:
        #     self.base = MLPBase(obs_shape[0], **base_kwargs)
        # else:
        #     raise NotImplementedError

        if action_space.__class__.__name__ == "Discrete":
            num_outputs = action_space.n
            self.dist = Categorical(self.base.output_size, num_outputs)
        elif action_space.__class__.__name__ == "Box":
            num_outputs = action_space.shape[0]
            self.dist = DiagGaussian(self.base.output_size, num_outputs)
        else:
            raise NotImplementedError

    @property
    def is_recurrent(self):
        return self.base.is_recurrent

    @property
    def recurrent_hidden_state_size(self):
        """Size of rnn_hx."""
        return self.base.recurrent_hidden_state_size

    def forward(self, inputs, rnn_hxs, masks):
        raise NotImplementedError

    def act(self, inputs, rnn_hxs, masks, deterministic=False, return_probs=False):
        value, actor_features, rnn_hxs = self.base(inputs, rnn_hxs, masks)
        dist = self.dist(actor_features)
        
        if deterministic:
            action = dist.mode()
        else:
            action = dist.sample()

        action_log_probs = dist.log_probs(action)
        dist_entropy = dist.entropy().mean()
        if self.random_act:
            bs = action.shape[0]
            act_idx = self.rng_act.choice(4, [bs,1], p = [0.5, 0.5, 0, 0])
            action_log_probs = 0 * action_log_probs + 1
            action[:] = torch.tensor(act_idx).to(self.device)
        
        if return_probs:
          probs = dist.probs
          return value, action, action_log_probs, rnn_hxs, probs
        return value, action, action_log_probs, rnn_hxs

    def get_value(self, inputs, rnn_hxs, masks):
        value, _, _ = self.base(inputs, rnn_hxs, masks)
        return value

    def evaluate_actions(self, inputs, rnn_hxs, masks, action):
        value, actor_features, rnn_hxs = self.base(inputs, rnn_hxs, masks)
        dist = self.dist(actor_features)

        action_log_probs = dist.log_probs(action)
        dist_entropy = dist.entropy().mean()

        return value, action_log_probs, dist_entropy, rnn_hxs


class NNBase(nn.Module):

    def __init__(self, recurrent, recurrent_input_size, hidden_size):
        super(NNBase, self).__init__()

        self._hidden_size = hidden_size
        self._recurrent = recurrent

        if recurrent:
            self.gru = nn.GRU(recurrent_input_size, hidden_size)
            for name, param in self.gru.named_parameters():
                if 'bias' in name:
                    nn.init.constant_(param, 0)
                elif 'weight' in name:
                    nn.init.orthogonal_(param)

    @property
    def is_recurrent(self):
        return self._recurrent

    @property
    def recurrent_hidden_state_size(self):
        if self._recurrent:
            return self._hidden_size
        return 1

    @property
    def output_size(self):
        return self._hidden_size

    def _forward_gru(self, x, hxs, masks):
        if x.size(0) == hxs.size(0):
            x, hxs = self.gru(x.unsqueeze(0), (hxs * masks).unsqueeze(0))
            x = x.squeeze(0)
            hxs = hxs.squeeze(0)
        else:
            # x is a (T, N, -1) tensor that has been flatten to (T * N, -1)
            N = hxs.size(0)
            T = int(x.size(0) / N)

            # unflatten
            x = x.view(T, N, x.size(1))

            # Same deal with masks
            masks = masks.view(T, N)

            # Let's figure out which steps in the sequence have a zero for any agent
            # We will always assume t=0 has a zero in it as that makes the logic cleaner
            has_zeros = ((masks[1:] == 0.0) \
                            .any(dim=-1)
                            .nonzero()
                            .squeeze()
                            .cpu())


            # +1 to correct the masks[1:]
            if has_zeros.dim() == 0:
                # Deal with scalar
                has_zeros = [has_zeros.item() + 1]
            else:
                has_zeros = (has_zeros + 1).numpy().tolist()

            # add t=0 and t=T to the list
            has_zeros = [0] + has_zeros + [T]


            hxs = hxs.unsqueeze(0)
            outputs = []
            for i in range(len(has_zeros) - 1):
                # We can now process steps that don't have any zeros in masks together!
                # This is much faster
                start_idx = has_zeros[i]
                end_idx = has_zeros[i + 1]

                rnn_scores, hxs = self.gru(
                    x[start_idx:end_idx],
                    hxs * masks[start_idx].view(1, -1, 1)
                )

                outputs.append(rnn_scores)

            # assert len(outputs) == T
            # x is a (T, N, -1) tensor
            x = torch.cat(outputs, dim=0)
            # flatten
            x = x.view(T * N, -1)
            hxs = hxs.squeeze(0)

        return x, hxs

class NavBase(NNBase):
    def __init__(self, observation_space, policy_type=None, recurrent=False,
      hidden_size=512, feat_dim=512, init_policy=True, bn_freeze=False, arch='resnet18'):
        super(NavBase, self).__init__(recurrent, feat_dim, hidden_size)
        assert(policy_type == 'Nav')
        init_ = lambda m: init(m,
            nn.init.orthogonal_,
            lambda x: nn.init.constant_(x, 0),
            nn.init.calculate_gain('relu'))
        num_inputs = observation_space.spaces['view'].shape[0]
        assert(init_policy)
        if arch == 'resnet18':
          self.image_cnn = Conditional_Net_RN5N(feat_dim)
          if bn_freeze:
            def set_bn_to_eval(m):
              if isinstance(m, nn.BatchNorm2d) or isinstance(m, nn.BatchNorm1d):
                m.eval()
                print('Setting to eval mode %s', str(m))
            self.image_cnn.apply(set_bn_to_eval)
        
        elif arch == 'small':
          self.image_cnn = nn.Sequential(
              init_(nn.Conv2d(3, 32, 8, stride=4)),
              nn.ReLU(),
              nn.MaxPool2d(2),
              init_(nn.Conv2d(32, 64, 4, stride=2)),
              nn.ReLU(),
              nn.MaxPool2d(2),
              init_(nn.Conv2d(64, 32, 3, stride=1)),
              nn.ReLU(),
              nn.MaxPool2d(2),
              Flatten(),
              init_(nn.Linear(32 * 2 * 2, feat_dim)),
              nn.ReLU()
          )
        # if init_policy:
        #   print('Loading weights from {:s}.'.format(full_load_path))
        #   self.image_cnn.load_state_dict(torch.load(full_load_path))
        
        total_size = 0
        for k in observation_space.spaces:
          total_size += observation_space.spaces[k].shape[0]
        total_size = total_size - num_inputs
        ks = sorted(set(observation_space.spaces.keys()) - set(['view']))
        print('Will concat and use with fc layer', ks)
        
        if total_size > 0:
          self.other_fc = nn.Sequential(
              init_(nn.Linear(total_size, 128)),
              nn.ReLU(),
              init_(nn.Linear(128, 256)),
              nn.ReLU(),
              init_(nn.Linear(256, hidden_size//2)),
              nn.ReLU(),
          )
        else:
          self.other_fc = None

        init_ = lambda m: init(m,
            nn.init.orthogonal_,
            lambda x: nn.init.constant_(x, 0))

        self.critic_linear = init_(nn.Linear(hidden_size, 1))

        self.train()

    def forward(self, inputs, rnn_hxs, masks):
        view = inputs['view']; x = self.image_cnn(view);
        if self.other_fc is not None:
          ks = sorted(set(inputs.keys()) - set(['view']))
          to_cat = []
          for k in ks:
            to_cat.append(inputs[k])
          others = torch.cat(to_cat, 1)
          xx = self.other_fc(others)
          x = torch.cat([x, xx], 1)

        if self.is_recurrent:
            x, rnn_hxs = self._forward_gru(x, rnn_hxs, masks)
        return self.critic_linear(x), x, rnn_hxs



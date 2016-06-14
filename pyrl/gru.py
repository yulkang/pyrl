from __future__ import absolute_import, division

import sys
from   collections import OrderedDict

import numpy as np

import theano
from   theano import tensor

from .          import matrixtools, nptools, theanotools
from .debug     import DEBUG
from .recurrent import Recurrent

from . import tasktools

configs_required = ['Nin', 'Nout']
configs_default  = {
    'alpha':   1,
    'N':       50,
    'p0':      1,
    'rho':     1.5,
    'f_out':   'softmax',
    'L2_r':    0.002,
    'L1_Wrec': 0,
    'L2_Wrec': 0,
    'fix':     [],
    'ei':      None
    }

class GRU(Recurrent):
    """
    Modified Gated Recurrent Units.

    """
    def get_dim(self, name):
        if name == 'Win':
            return (self.Nin, 3*self.N)
        if name == 'bin':
            return 3*self.N
        if name == 'Wrec_gates':
            return (self.N, 2*self.N)
        if name == 'Wrec':
            return (self.N, self.N)
        if name == 'Wout':
            return (self.N, self.Nout)
        if name == 'bout':
            return self.Nout
        if name == 'x0':
            return self.N

        raise ValueError

    def __init__(self, config, params=None, masks=None, seed=1):
        super(GRU, self).__init__('gru')

        #=================================================================================
        # Config
        #=================================================================================

        self.config = {}

        # Required
        for k in configs_required:
            if k not in config:
                print("[ GRU ] Error: {} is required.".format(k))
                sys.exit()
            self.config[k] = config[k]

        # Defaults available
        for k in configs_default:
            if k in config:
                self.config[k] = config[k]
            else:
                self.config[k] = configs_default[k]

        #=================================================================================
        # Activations
        #=================================================================================

        # Hidden
        self.f_hidden    = tensor.nnet.relu
        self.firing_rate = nptools.relu

        # Output
        if self.config['f_out'] == 'softmax':
            self.f_out  = theanotools.softmax
            self.f_out3 = theanotools.softmax3
        elif self.config['f_out'] == 'linear':
            self.f_out  = (lambda x: x)
            self.f_out3 = self.f_out
        else:
            raise ValueError(self.config['f_out'])

        #=================================================================================
        # Network shape
        #=================================================================================

        self.Nin  = self.config['Nin']
        self.N    = self.config['N']
        self.Nout = self.config['Nout']

        #=================================================================================
        # Initialize parameters
        #=================================================================================

        #self.config['ei'], EXC, INH = tasktools.generate_ei(self.N)

        # Masks
        '''
        if self.config['ei'] is not None:
            inh, = np.where(self.config['ei'] < 0)
            for k in ['Wrec_gates', 'Wrec']:#, 'Wout']:
                self.masks[k]       = np.ones(self.get_dim(k))
                self.masks[k][inh] *= -1
                #self.masks[k]       = theanotools.shared(self.masks[k])
        '''

        if params is None:
            #-----------------------------------------------------------------------------
            # Random number generator
            #-----------------------------------------------------------------------------

            if DEBUG:
                print("[ GRU ] seed = {}".format(seed))
            rng = np.random.RandomState(seed)

            #-----------------------------------------------------------------------------
            # Connection masks
            #-----------------------------------------------------------------------------

            masks = {}

            # In-degree
            K   = int(self.config['p0']*self.N)
            idx = np.arange(self.N)

            # Wrec
            M = np.zeros(self.get_dim('Wrec'))
            for j in xrange(M.shape[1]):
                M[rng.permutation(idx)[:K],j] = 1
            masks['Wrec'] = M

            # Wrec (gates)
            M = np.zeros(self.get_dim('Wrec_gates'))
            for j in xrange(M.shape[1]):
                M[rng.permutation(idx)[:K],j] = 1
            masks['Wrec_gates'] = M

            #-----------------------------------------------------------------------------
            # Network parameteres
            #-----------------------------------------------------------------------------

            params = OrderedDict()
            if self.config['ei'] is None:
                params['Win']        = rng.normal(size=self.get_dim('Win'))
                params['bin']        = np.zeros(self.get_dim('bin'))
                params['Wrec_gates'] = rng.normal(size=self.get_dim('Wrec_gates'))
                params['Wrec']       = rng.normal(size=self.get_dim('Wrec'))
                params['Wout']       = np.zeros(self.get_dim('Wout'))
                params['bout']       = np.zeros(self.get_dim('bout'))
                params['x0']         = 0.5*np.ones(self.get_dim('x0'))
            else:
                params['Win']        = rng.normal(size=self.get_dim('Win'))
                params['bin']        = np.zeros(self.get_dim('bin'))
                #params['Wrec_gates'] = rng.normal(size=self.get_dim('Wrec_gates'))
                #params['Wrec'] = rng.normal(size=self.get_dim('Wrec'))
                params['Wout']       = np.zeros(self.get_dim('Wout'))
                params['bout']       = np.zeros(self.get_dim('bout'))
                params['x0']         = 0.2*np.ones(self.get_dim('x0'))
                #params['Wout']       = 0.1*np.ones(self.get_dim('Wout'))

                exc, = np.where(self.config['ei'] > 0)
                inh, = np.where(self.config['ei'] < 0)

                k     = 2
                theta = 0.1/k
                params['Wrec_gates']  = rng.gamma(k, theta, size=self.get_dim('Wrec_gates'))
                params['Wrec'] = rng.gamma(k, theta, size=self.get_dim('Wrec'))

                for i in xrange(params['Wrec_gates'].shape[1]):
                    totE = np.sum(params['Wrec_gates'][exc,i])
                    totI = np.sum(params['Wrec_gates'][inh,i])
                    params['Wrec_gates'][inh,i] *= totE/totI
                for i in xrange(params['Wrec'].shape[1]):
                    totE = np.sum(params['Wrec'][exc,i])
                    totI = np.sum(params['Wrec'][inh,i])
                    params['Wrec'][inh,i] *= totE/totI

            # Desired spectral radius
            rho = self.config['rho']

            Wrec_gates = params['Wrec_gates'].copy()
            if 'Wrec_gates' in masks:
                Wrec_gates *= masks['Wrec_gates']
            Wrec = params['Wrec'].copy()
            if 'Wrec' in masks:
                Wrec *= masks['Wrec']

            rho0 = matrixtools.spectral_radius(Wrec_gates[:,:self.N])
            params['Wrec_gates'][:,:self.N] *= rho/rho0

            rho0 = matrixtools.spectral_radius(Wrec_gates[:,self.N:])
            params['Wrec_gates'][:,self.N:] *= rho/rho0

            rho0 = matrixtools.spectral_radius(Wrec)
            params['Wrec'] *= rho/rho0

        #=================================================================================
        # Display spectral radii
        #=================================================================================

        Wrec_gates = params['Wrec_gates'].copy()
        if 'Wrec_gates' in masks:
            Wrec_gates *= masks['Wrec_gates']
        Wrec = params['Wrec'].copy()
        if 'Wrec' in masks:
            Wrec *= masks['Wrec']

        rho0 = matrixtools.spectral_radius(Wrec_gates[:,:self.N])
        print("rho = {}".format(rho0))
        #params['Wrec_gates'][:,:self.N] *= rho/rho0

        rho0 = matrixtools.spectral_radius(Wrec_gates[:,self.N:])
        print("rho = {}".format(rho0))
        #params['Wrec_gates'][:,self.N:] *= rho/rho0

        rho0 = matrixtools.spectral_radius(Wrec)
        print("rho = {}".format(rho0))
        #params['Wrec'] *= rho/rho0

        #=================================================================================
        # Give to Theano
        #=================================================================================

        # Share
        for k, v in params.items():
            self.params[k] = theanotools.shared(v, k)
        for k, v in masks.items():
            self.masks[k] = theanotools.shared(v)

        # Fixed parameters
        if DEBUG and self.config['fix']:
            print("[ GRU ] Fixed parameters: " + ', '.join(self.config['fix']))

        # Trainable parameters
        self.trainables = [self.params[k]
                           for k in self.params if k not in self.config['fix']]

        #=================================================================================
        # Setup
        #=================================================================================

        # Leak
        self.alpha = self.config['alpha']
        print("[ GRU ] alpha = {}".format(self.alpha))

        #=================================================================================
        # Define a step
        #=================================================================================

        def step(u, q, x_tm1, alpha, Win, bin, Wrec_gates, Wrec):
            inputs_t     = u.dot(Win) + bin
            state_inputs = inputs_t[:,:self.N]
            gate_inputs  = inputs_t[:,self.N:]

            r_tm1 = self.f_hidden(x_tm1)

            gate_values   = tensor.nnet.sigmoid(r_tm1.dot(Wrec_gates) + gate_inputs)
            update_values = gate_values[:,:self.N]
            g = gate_values[:,self.N:]
            x_t = ((1 - alpha*update_values)*x_tm1
                   + alpha*update_values*((g*r_tm1).dot(Wrec) + state_inputs + q))

            return x_t

        self.step         = step
        self.step_params  = [self.alpha]
        self.step_params += [self.get(k)
                             for k in ['Win', 'bin', 'Wrec_gates', 'Wrec']]

    def get_regs(self, x0_, x, M):
        """
        Regularization terms.

        """
        regs = 0

        #=================================================================================
        # L1 recurrent weights
        #=================================================================================

        L1_Wrec = self.config['L1_Wrec']
        if L1_Wrec > 0:
            print("L1_Wrec = {}".format(L1_Wrec))

            W    = self.get('Wrec')
            reg  = tensor.sum(abs(W))
            size = tensor.prod(W.shape)

            #W     = self.get('Wrec_gates')
            #reg  += tensor.sum(abs(W))
            #size += tensor.prod(W.shape)

            regs += L1_Wrec * reg/size

        #=================================================================================
        # L2 recurrent weights
        #=================================================================================

        L2_Wrec = self.config['L2_Wrec']
        if L2_Wrec > 0:
            print("L2_Wrec = {}".format(L2_Wrec))

            W    = self.get('Wrec')
            reg  = tensor.sum(tensor.sqr(W))
            size = tensor.prod(W.shape)

            W     = self.get('Wrec_gates')
            reg  += tensor.sum(tensor.sqr(W))
            size += tensor.prod(W.shape)

            regs += L2_Wrec * reg/size

        #=================================================================================
        # Firing rates
        #=================================================================================

        L2_r = self.config['L2_r']
        if L2_r  > 0:
            M_ = (tensor.tile(M.T, (x.shape[-1], 1, 1))).T
            x_all = tensor.concatenate(
                [x0_.reshape((1, x0_.shape[0], x0_.shape[1])), x],
                axis=0
                )
            r = self.f_hidden(x_all)

            regs += L2_r * tensor.sum(tensor.sqr(r)*M_)/tensor.sum(M_)

        #=================================================================================

        return regs
#!/bin/bash

module purge
module load pbs dot nco/4.6.4 intel-fc/12.1.9.293 intel-cc/12.1.9.293
module use /g/data3/hh5/public/modules
module load conda/analysis27
module load wgrib2

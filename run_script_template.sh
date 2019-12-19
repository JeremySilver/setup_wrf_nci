#!/bin/bash

#PBS -N ${RUNSHORT}_${STARTDATE}
#PBS -l walltime=12:00:00
#PBS -l mem=192GB
#PBS -l ncpus=48
#PBS -j oe
#PBS -q normal
#PBS -l wd

module purge
module load dot
module load pbs
source load_wrf_env.sh


ulimit -s unlimited
cd ${RUN_DIR}

echo running with $PBS_NCPUS mpi ranks
time mpirun -np $PBS_NCPUS ./wrf.exe >& wrf.log

if [ ! -e rsl.out.0000 ] ; then
    echo "wrf.exe did not complete successfully - exiting"
    exit
fi

issuccess=`grep -c "SUCCESS COMPLETE WRF" rsl.out.0000`
echo $issuccess

if [ "$issuccess" -eq 0 ] ; then
    echo "wrf.exe did not complete successfully - exiting"
    exit
fi

# We don't need the linked restart files any more
find . -name 'wrfrst*' -type f -delete

if [ "$issuccess" -gt 0 ] ; then
   echo "cleaning up now"
   qsub cleanup.sh
fi

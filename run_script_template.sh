#!/bin/bash

#PBS -N ${RUNSHORT}_${STARTDATE}
#PBS -l walltime=12:00:00
#PBS -l mem=128GB
#PBS -l ncpus=28
#PBS -j oe
#PBS -q normalbw
#PBS -l wd

module purge
module load dot
module load pbs
module load intel-cc/17.0.1.132
module load intel-fc/17.0.1.132
module load netcdf/4.3.3.1
module load openmpi/1.10.2
module load nco
module load hdf5/1.8.10


ulimit -s unlimited
cd ${RUN_DIR}

echo running with $PBS_NCPUS mpi ranks
time mpirun -np $PBS_NCPUS -report-bindings ./wrf.exe >& wrf.log

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

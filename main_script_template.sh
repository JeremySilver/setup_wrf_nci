#!/bin/bash
#PBS -N ${RUNNAME}
#PBS -l walltime=48:00:00
#PBS -l mem=128GB
#PBS -l ncpus=64
#PBS -j oe
#PBS -q normal
#PBS -l wd

# Submit WRF for a group of consecutive days. Wait for each job to
# finish before starting the next one.

echo Start date is ${STARTDATE}
echo Run directory is ${RUN_DIR}

[ ! -e ${RUN_DIR}/${STARTDATE}/ ] && echo "directory ${RUN_DIR}/${STARTDATE} not found - exiting" && exit
cd ${RUN_DIR}/${STARTDATE}/

if [ ${runAsOneJob} == "true" ] ; then
      chmod u+x run.sh
      ./run.sh
else
      job=`qsub run.sh`
      echo "job now running is $job"
fi

n=1

startdate=${STARTDATE}

while [ $n -lt ${njobs} ]; do

  # Get the next date
  start_date=`echo $startdate | cut -b 1-8`
  start_hour=`echo $startdate | cut -b 9-10`
  startdate=`date -u +%Y%m%d%H -d "$start_date+$start_hour hours+${nhours} hours UTC"`
 
  echo $startdate
 
  # Go into the next directory
  cd ${RUN_DIR}/$startdate/

  # Submit run
  if [ ${runAsOneJob} == "true" ] ; then
      chmod u+x run.sh
      ./run.sh
  elif [ ${NUDGING} == "true" ] ; then
      job_next=`qsub -W depend=afterok:$job run.sh`
      echo "$job_next depends on $job"
      job=$job_next
  else
      job_next=`qsub run.sh`
      echo "Job $job_next now queued"
      job=$job_next
  fi
  let n=n+1
done

cd ${RUN_DIR}



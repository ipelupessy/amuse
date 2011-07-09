import sys
import unittest
import numpy
import random
import collections
import getopt
import os

from amuse.support.units import nbody_system
from amuse.support.units import units
from amuse.support.data import core
from amuse.support.data import particle_attributes
from amuse.support.codes.core import is_mpd_running
from amuse.ext.plummer import MakePlummerModel
from amuse.ext.salpeter import new_salpeter_mass_distribution_nbody

from amuse.support.data import trees

from amuse.community.ph4.interface import ph4 as grav
#from amuse.community.phiGRAPE.interface import PhiGRAPE as grav
#from amuse.community.hermite0.interface import Hermite as grav
from amuse.community.newsmallN.interface import smallN

def print_log(time, gravity, E0 = 0.0 | nbody_system.energy):
    M = gravity.total_mass
    U = gravity.potential_energy
    T = gravity.kinetic_energy
    try:
        Ebin = gravity.get_binary_energy()
    except:
        Ebin = 0 | nbody_system.energy
    Etop = T + U
    E = Etop + Ebin
    if E0 == 0 | nbody_system.energy: E0 = E
    Rv = -0.5*M*M/U
    Q = -T/U
    print ""
    print "time =", time.number, " energy = ", E.number, \
	" dE/E0 = ", (E/E0 - 1).number
    print '%s %.4f %.6f %.6f %.6f %.6f %.6f %.6f %.6f' % \
	("%%", time.number, M.number, T.number, U.number, \
         E.number, Ebin.number, Rv.number, Q.number)
    sys.stdout.flush()
    return E

def run_smallN(
        particles,
        end_time = 1000 | nbody_system.time,
        delta_t = 10 | nbody_system.time,
        accuracy_parameter = 0.1
    ):

    gravity = smallN(redirection = "none") # , debugger="gdb")
    gravity.initialize_code()
    gravity.parameters.set_defaults()
    gravity.parameters.timestep_parameter = accuracy_parameter

    time = 0 | nbody_system.time

    print "adding particles to smallN"
    sys.stdout.flush()
    gravity.set_time(time);
    gravity.particles.add_particles(particles)
    print "committing particles"
    gravity.commit_particles()

    print ''
    print "smallN: number_of_stars =", len(particles)
    print "smallN: evolving to time =", end_time.number, 
    print " in steps of", delta_t.number
    sys.stdout.flush()
    
    E0 = print_log(time, gravity)
    
    # Channel to copy values from the code to the set in memory.
    channel = gravity.particles.new_channel_to(particles)

    while time < end_time:
        time += delta_t
        gravity.evolve_model(time)
        print_log(time, gravity, E0)

        print "smallN time =", gravity.get_time().number
        over = gravity.is_over()
        if over.number:
            print 'interaction is over\n'
            gravity.update_particle_tree()
            gravity.update_particle_set()
            gravity.particles.synchronize_to(particles)
            channel.copy()
            gravity.stop()
            return particles
    
        sys.stdout.flush()
    
    gravity.stop()
    raise Exception("Did not finish the small-N simulation "
		    +"before end time {0}".format(end_time))

def test_ph4(infile = None, number_of_stars = 40,
             end_time = 10 | nbody_system.time,
             delta_t = 1 | nbody_system.time,
             n_workers = 1, use_gpu = 1, gpu_worker = 1,
             accuracy_parameter = 0.1,
             softening_length = -1 | nbody_system.length,
             manage_encounters = 1):

    if infile != None: print "input file =", infile
    print "end_time =", end_time.number
    print "delta_t =", delta_t.number
    print "n_workers =", n_workers
    print "use_gpu =", use_gpu
    print "manage_encounters =", manage_encounters
    print "\ninitializing the gravity module"
    sys.stdout.flush()

    # Note that there are actually three GPU options to test:
    #
    #	1. use the GPU code and allow GPU use (default)
    #	2. use the GPU code but disable GPU use (-g)
    #	3. use the non-GPU code (-G)

    if gpu_worker == 1:
        try:
            gravity = grav(number_of_workers = n_workers,
                           redirection = "none", mode = "gpu")
        except Exception as ex:
            gravity = grav(number_of_workers = n_workers, redirection = "none")
    else:
	gravity = grav(number_of_workers = n_workers, redirection = "none")

    gravity.initialize_code()
    gravity.parameters.set_defaults()

    #-----------------------------------------------------------------

    if infile == None:

        print "making a Plummer model"
        stars = MakePlummerModel(number_of_stars).result

        id = numpy.arange(number_of_stars)
        stars.id = id+1 | units.none

        print "setting particle masses and radii"
	#stars.mass = (1.0 / number_of_stars) | nbody_system.mass
        scaled_mass = new_salpeter_mass_distribution_nbody(number_of_stars) 
        stars.mass = scaled_mass
        stars.radius = 0.0 | nbody_system.length

        print "centering stars"
        stars.move_to_center()
        print "scaling stars to virial equilibrium"
        stars.scale_to_standard(smoothing_length_squared
                                    = gravity.parameters.epsilon_squared)

        time = 0.0 | nbody_system.time
        sys.stdout.flush()

    else:

        # Read the input data.  Units are dynamical.

        print "reading file", infile

        id = []
        mass = []
        pos = []
        vel = []

        f = open(infile, 'r')
	count = 0
        for line in f:
            if len(line) > 0:
                count += 1
		cols = line.split()
                if count == 1: snap = int(cols[0])
		elif count == 2: number_of_stars = int(cols[0])
		elif count == 3: time = float(cols[0]) | nbody_system.time
		else:
                    if len(cols) >= 8:
                        id.append(int(cols[0]))
                        mass.append(float(cols[1]))
                        pos.append((float(cols[2]),
                                    float(cols[3]), float(cols[4])))
                        vel.append((float(cols[5]),
                                    float(cols[6]), float(cols[7])))
	f.close()

        stars = core.Particles(number_of_stars)
        stars.id = id | units.none
        stars.mass = mass | nbody_system.mass
        stars.position = pos | nbody_system.length
        stars.velocity = vel | nbody_system.speed
        stars.radius = 0. | nbody_system.length

    # print "IDs:", stars.id.number
    sys.stdout.flush()

    #-----------------------------------------------------------------

    if softening_length == -1 | nbody_system.length:
        eps2 = 0.25*(float(number_of_stars))**(-0.666667) \
			| nbody_system.length**2
    else:
        eps2 = softening_length*softening_length

    gravity.parameters.timestep_parameter = accuracy_parameter
    gravity.parameters.epsilon_squared = eps2
    gravity.parameters.use_gpu = use_gpu
    gravity.parameters.manage_encounters = manage_encounters

    print "adding particles"
    # print stars
    sys.stdout.flush()
    gravity.particles.add_particles(stars)
    gravity.commit_particles()

    print ''
    print "number_of_stars =", number_of_stars
    print "evolving to time =", end_time.number, \
          "in steps of", delta_t.number
    sys.stdout.flush()

    E0 = print_log(time, gravity)
    
    # Channel to copy values from the code to the set in memory.
    channel = gravity.particles.new_channel_to(stars)
    
    stopping_condition = gravity.stopping_conditions.collision_detection
    stopping_condition.enable()
    
    while time < end_time:
        time += delta_t
        gravity.evolve_model(time)

        if stopping_condition.is_set():
            star1 = stopping_condition.particles(0)[0]
            star2 = stopping_condition.particles(1)[0]
            print '\nstopping condition set at time', \
                gravity.get_time().number,'for:\n'
            print star1
            print ''
            print star2
            print ''
            p = core.Particles(0)
            p.add_particle(star1)
            p.add_particle(star2)
            print p
            run_smallN(p)
            raise Exception("not done yet")

        # Ensure that the stars list is consistent with the internal
        # data in the module.

        ls = len(stars)

	# Update the bookkeeping: synchronize stars with the module data.

        try:
            gravity.update_particle_set()
            gravity.particles.synchronize_to(stars)
        except:
            pass
    
        # Copy values from the module to the set in memory.

        channel.copy()
    
        # Copy the index (ID) as used in the module to the id field in
        # memory.  The index is not copied by default, as different
        # codes may have different indices for the same particle and
        # we don't want to overwrite silently.

        channel.copy_attribute("index_in_code", "id")

        if len(stars) != ls:
            if 0:
                print "stars:"
                for s in stars:
                    print " ", s.id.number, s.mass.number, \
			       s.x.number, s.y.number, s.z.number
            else:
		print "number of stars =", len(stars)
            sys.stdout.flush()

        print_log(time, gravity, E0)
        sys.stdout.flush()

    print ''
    gravity.stop()

if __name__ == '__main__':

    infile = None
    N = 100
    t_end = 1000.0 | nbody_system.time
    delta_t = 10.0 | nbody_system.time
    n_workers = 1
    use_gpu = 0
    gpu_worker = 0
    accuracy_parameter = 0.1
    softening_length = 0  | nbody_system.length
    random_seed = -1
    manage_encounters = 4

    try:
        opts, args = getopt.getopt(sys.argv[1:], "a:c:d:e:f:gGn:s:t:w:")
    except getopt.GetoptError, err:
        print str(err)
        sys.exit(1)

    for o, a in opts:
        if o == "-a":
            accuracy_parameter = float(a)
        elif o == "-c":
            manage_encounters = int(a)
        elif o == "-d":
            delta_t = float(a) | nbody_system.time 
        elif o == "-e":
            softening_length = float(a) | nbody_system.length
        elif o == "-f":
            infile = a
        elif o == "-g":
            use_gpu = 0
        elif o == "-G":
            use_gpu = 0
            gpu_worker = 0
        elif o == "-n":
            N = int(a)
        elif o == "-s":
            random_seed = int(a)
        elif o == "-t":
            t_end = float(a) | nbody_system.time
        elif o == "-w":
            n_workers = int(a)
        else:
            print "unexpected argument", o

    if random_seed <= 0:
        numpy.random.seed()
        random_seed = numpy.random.randint(1, pow(2,31)-1)
    numpy.random.seed(random_seed)
    print "random seed =", random_seed

    assert is_mpd_running()
    test_ph4(infile, N, t_end, delta_t, n_workers,
             use_gpu, gpu_worker,
             accuracy_parameter, softening_length,
             manage_encounters)

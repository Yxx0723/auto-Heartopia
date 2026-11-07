"""Continuous input for existing saved-point collection; no native guidance API."""
import math

from utils.core import thresholds as policy
from utils.core.coordinate_route import distance
from utils.drivers.guidance_input_lease import GuidanceInputLease
from utils.flows.coordinate_player import _Follower, _MovementRetry


class CollectionFollower(_Follower):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.input_lease=GuidanceInputLease(self.device,self.controls,self.window,
            self.data['client_size'],self.clock.monotonic)
        self.move_deadline=float('-inf')
        self._applying_movement=False
        self._normal_movement=False
        self.cycle_metrics={}
        self._approved_context=None
        self.move_conditions={}
        self.sleep_overrun=0.

    def follow(self):
        self.input_lease.start()
        try:
            return super().follow()
        finally:
            self.input_lease.close()
            self.keys.clear()

    def release(self,reason='controller'):
        self._memory_anchor=None
        self.input_lease.release(reason,active=bool(self.keys))
        self.keys.clear()
        self._normal_movement=False
        self._approved_context=None
        self.navigator.reset_steering()

    def planning_release(self):
        reason=getattr(self.navigator,'release_reason','planning_or_map_change')
        # Continue ONLY the already checked input until its original deadline.
        # Computation never renews permission or approves a new movement segment.
        if reason in ('map_build','path_search'):
            self.guard()
            now=self.clock.monotonic()
            expiry=self.navigator.collision_deadline(now)
            with self.input_lease.lock:
                deadline=self.input_lease.deadline
                if self._normal_movement and self.keys and deadline is not None \
                        and now < deadline and expiry is not None and now < expiry \
                        and self._approved_context is not None \
                        and self._approved_context==self.movement_context():
                    return
        self.release(reason)

    def movement_context(self):
        nav=self.navigator
        if nav.obstacles is None:
            return None
        scene=nav.scene or {}
        return (scene.get('cache_context'),scene.get('terrain_revision'),
                nav.scene_signature,nav.reference_revision,nav.risks.revision,
                nav.obstacles.height)

    def read(self,force=False):
        previous=self.input_lease.set_phase('position_read')
        try:
            # A shortened wait can start the next cycle before the old 50 ms
            # position cache expires. Reusing that sample cannot renew its age.
            return super().read(force or self._normal_movement)
        finally:
            self.input_lease.set_phase(previous)

    def read_direction(self):
        previous=self.input_lease.set_phase('direction_read')
        try:
            return super().read_direction()
        finally:
            self.input_lease.set_phase(previous)

    def recover_no_path(self,target,basis,segment_start,index,total):
        self.release('path_escape')
        if basis is None:
            return False
        position=self.read(force=True)
        self.recovery.begin(position)
        self.travel_progress.begin_probe(position)
        origin=self.recovery.origin
        goal=self.navigator.path_escape_target(position,target,self.recovery.attempts,
            origin,self.guard,self.planning_release)
        self.report('recovering','局部路径不可达，检查有限退让后重新寻路',
            waypoint=index+1,waypoint_count=total,recovery_phase='path_escape',
            recovery_attempt=self.recovery.attempts,recovery_result='no_safe_step' if goal is None else 'checked_step')
        if goal is None:
            return False
        # Reuse the bounded per-pulse, full-geometry recovery checks. A failed
        # plan neither learns a collision wall nor changes the saved waypoint.
        try:
            return self.recovery_leg(goal,basis,origin,target)
        finally:
            self.release('path_escape_complete')
            self.navigator.invalidate()

    def guard(self):
        self.input_lease.poll()
        with self.input_lease.lock:
            if self.input_lease.error:
                raise OSError('独立按键释放失败') from self.input_lease.error
            if self.input_lease.tripped:
                self.keys.clear()
                self._normal_movement=False
                self.input_lease.tripped=False
                raise _MovementRetry()
        return super().guard()

    def set_keys(self,keys):
        # F, meals, tool menus and recovery retain the existing bounded flow.
        # None of them may accidentally inherit a normal walking permit.
        if not self._applying_movement and self._normal_movement:
            self.release('movement_to_action')
        return super().set_keys(keys)

    def steering_travel(self,position,target,precise=False):
        now=self.clock.monotonic()
        expiry=self.navigator.collision_deadline(now)
        if expiry is None or self.position_at is None:
            self.release('collision_not_ready')
            raise _MovementRetry()
        speed=max(3.,self.calibration_speed)
        self.move_conditions={'position':self.position_at+policy.COORD_GUIDANCE_INPUT_LEASE_SECONDS,
            'guidance':None,'collision':expiry,
            'target_distance':self.position_at+distance(position,target)/speed*.5,
            'input_lease':now+policy.COORD_GUIDANCE_INPUT_LEASE_SECONDS}
        self.move_deadline=min(value for value in self.move_conditions.values() if value is not None)
        return min(max(.15,speed*(policy.COORD_GUIDANCE_INPUT_LEASE_SECONDS
            +policy.COORD_GUIDANCE_INPUT_POLL_SECONDS)),max(.02,distance(position,target)))

    def apply_movement(self,keys,position,target):
        lock_at=self.clock.monotonic()
        with self.input_lease.lock:
            self.cycle_metrics['input_lock_wait_seconds']=max(0.,self.clock.monotonic()-lock_at)
            self.guard()
            if not keys:
                self.release('steering_idle')
                return
            try:
                self.input_lease.arm(self.move_deadline,self.move_conditions)
            except ValueError as exc:
                self.release('permit_not_ready')
                raise _MovementRetry() from exc
            self._applying_movement=True
            self._normal_movement=True
            try:
                self.set_keys(keys)
                self.input_lease.observe_keys(self.keys)
                self._approved_context=self.movement_context()
                self.cycle_metrics['permit_remaining_at_arm']=max(0.,
                    self.move_deadline-self.clock.monotonic())
            except BaseException:
                self.release('input_exception')
                raise
            finally:
                self._applying_movement=False

    def wait_movement(self,started,remaining,final):
        now=self.clock.monotonic()
        work=max(0.,now-started)
        delay=max(0.,policy.COORD_SAMPLE_SECONDS-work)
        with self.input_lease.lock:
            deadline=self.input_lease.deadline
        if deadline is not None and delay:
            available=deadline-now
            if available<=policy.COORD_GUIDANCE_INPUT_POLL_SECONDS:
                self.release('cycle_budget_exhausted')
                delay=policy.COORD_GUIDANCE_INPUT_POLL_SECONDS
            else:
                # Leave time for the next validation, rather than waking only
                # one watchdog poll before expiry. Short cycle sleep is fine.
                reserve=max(policy.COORD_GUIDANCE_INPUT_POLL_SECONDS,
                            min(policy.COORD_SAMPLE_SECONDS,work+self.sleep_overrun))
                delay=min(delay,max(.001,available-reserve-policy.COORD_GUIDANCE_INPUT_POLL_SECONDS))
        self.cycle_metrics.update(cycle_work_seconds=work,cycle_sleep_requested=delay)
        self.input_lease.set_phase('sample_sleep')
        if delay:
            self.clock.sleep(math.nextafter(now+delay,math.inf)-now)
        self.cycle_metrics['cycle_sleep_actual']=max(0.,self.clock.monotonic()-now)
        self.sleep_overrun=max(0.,self.cycle_metrics['cycle_sleep_actual']-delay)
        self.cycle_metrics['cycle_sleep_overrun']=self.sleep_overrun
        self.input_lease.set_phase('cycle_control')

    def report(self,state,message,**extra):
        extra.setdefault('input_continuity',self.input_lease.metrics())
        extra.setdefault('navigation_timings',dict(self.navigator.last_timings))
        extra.setdefault('steering_metrics',dict(self.navigator.steering_stats))
        extra.setdefault('collection_control',dict(self.cycle_metrics))
        old=self.input_lease.set_phase('report')
        try:
            return super().report(state,message,**extra)
        finally:
            self.input_lease.set_phase(old)

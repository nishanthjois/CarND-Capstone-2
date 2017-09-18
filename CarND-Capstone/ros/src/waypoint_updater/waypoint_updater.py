#!/usr/bin/env python

import rospy
from std_msgs.msg import Int32
from geometry_msgs.msg import PoseStamped
from styx_msgs.msg import Lane, Waypoint
import tf

import math
import time

'''
This node will publish waypoints from the car's current position to some `x` distance ahead.

As mentioned in the doc, you should ideally first implement a version which does not care
about traffic lights or obstacles.

Once you have created dbw_node, you will update this node to use the status of traffic lights too.

Please note that our simulator also provides the exact location of traffic lights and their
current status in `/vehicle/traffic_lights` message. You can use this message to build this node
as well as to verify your TL classifier.

TODO (for Yousuf and Aaron): Stopline location for each traffic light.
'''

LOOKAHEAD_WPS = 400 # Number of waypoints we will publish. You can change this number
TIMEOUT_VALUE = 10.0
ONE_MPH = 0.44704

class WaypointUpdater(object):
    def __init__(self):
        rospy.loginfo('WaypointUpdater::__init__ - Start')

        rospy.init_node('waypoint_updater')
        rospy.Subscriber('/current_pose', PoseStamped, self.pose_cb)
        rospy.Subscriber('/base_waypoints', Lane, self.waypoints_cb)

        # TODO: Add a subscriber for /traffic_waypoint and /obstacle_waypoint below
        rospy.Subscriber('/traffic_waypoint', Int32, self.traffic_cb)
        # commenting the two below for the time being until clarification about whether
        # is needed or not
        #rospy.Subscriber('/obstacle_waypoint', , self.obstacle_cb)

        self.final_waypoints_pub = rospy.Publisher('final_waypoints', Lane, queue_size=1)

        # TODO: Add other member variables you need below
        self.tf_listener = tf.TransformListener()

        # The car's current position
        self.pose = None

        # The car's current velocity
        self.velocity = 0.0
        self.velocity_timestamp = None
        self.velocity_timestamp_position = None

        # The timestamp of the last traffic_waypoint
        self.traffic_waypoint_timestamp = 0.0

        # Define how many seconds we wait to start driving after we did not receive a traffic_waypoint
        self.red_light_tresh = 0.2

        # The index of the waypoint in the base_waypoints list, which is closest to the traffic light
        self.light_waypoint_index = None
        # The approximate distance from the stop line to the traffic light
        self.light_distance_thresh = 30.0

        # The car's distance to the traffic light when the car started the slowing down process
        self.car_distance_to_tl_when_car_started_to_slow_down = None
        self.car_velocity_when_car_started_to_slow_down = None

        # first waypoint index at the previous iteration
        self.prev_first_wpt_index = 0

        self.default_velocity = rospy.get_param('~velocity', 1) * ONE_MPH

        rospy.spin()

    def pose_cb(self, msg):
        pose_timestamp = msg.header.stamp.secs + msg.header.stamp.nsecs / 1.0e9
        if self.pose != None:
            # Estimate the current speed (what is 0.2??)
            if pose_timestamp - self.velocity_timestamp > 0.2:
                distance = self.distance(self.velocity_timestamp_position, msg.pose.position)
                time_diff = (pose_timestamp - self.velocity_timestamp)
                self.velocity = distance / time_diff

        self.velocity_timestamp = pose_timestamp
        self.velocity_timestamp_position = msg.pose.position
        self.pose = msg

    def waypoints_cb(self, waypoints):
        # We cannot produce waypoints without the car's position
        if self.pose == None:
            return

        first_wpt_index = -1
        min_wpt_distance = float('inf')
        num_waypoints_in_list = len(waypoints.waypoints)

        # Gererate an empty lane to store the final_waypoints
        lane = Lane()
        lane.header.frame_id = waypoints.header.frame_id
        lane.header.stamp = rospy.Time(0)
        lane.waypoints = []

        # Iterate through the complete set of waypoints until we found the closest
        distance_decreased = False
        #rospy.loginfo('Started at waypoint index: %s', self.prev_first_wpt_index)
        #start_time = time.time()
        for index, waypoint in enumerate(waypoints.waypoints[self.prev_first_wpt_index:] + waypoints.waypoints[:self.prev_first_wpt_index], start=self.prev_first_wpt_index):
            current_wpt_distance = self.distance(self.pose.pose.position, waypoint.pose.pose.position)
            if distance_decreased and current_wpt_distance > min_wpt_distance:
                break
            if current_wpt_distance > 0 and current_wpt_distance < min_wpt_distance:
                min_wpt_distance = current_wpt_distance
                first_wpt_index = index
                distance_decreased = True

        if first_wpt_index >= num_waypoints_in_list:
            rospy.loginfo("BUUUGGG %s total %s", first_wpt_index, num_waypoints_in_list)
            first_wpt_index %= num_waypoints_in_list

        if first_wpt_index == -1:
            rospy.logwarn('WaypointUpdater::waypoints_cb - No waypoints ahead of ego were found... seems that the car went off course')
        else:
            # Transform first waypoint to car coordinates
            waypoints.waypoints[first_wpt_index].pose.header.frame_id = waypoints.header.frame_id
            self.tf_listener.waitForTransform("/base_link", "/world", rospy.Time(0), rospy.Duration(TIMEOUT_VALUE))
            transformed_waypoint = self.tf_listener.transformPose("/base_link", waypoints.waypoints[first_wpt_index].pose)

            self.prev_first_wpt_index = first_wpt_index

            # Prepare for calculating velocity:
            slow_down = False
            reached_zero_velocity = False
            car_distance_to_tl = -1.
            light_waypoint = None
            # If the last traffic_waypoint message is newer than the threshold, we might need to the car.
            if time.time() - self.traffic_waypoint_timestamp < self.red_light_tresh:
                light_waypoint = waypoints.waypoints[self.light_waypoint_index]
                # The approximate distance from the stop line to the traffic light
                car_distance_to_tl = self.distance(self.pose.position, light_waypoint.pose.pose.position) - self.light_distance_thresh
                if car_distance_to_tl > 0:
                    # Estimate whether the car cannot cross the stop line on yellow (in less than one and a half seconds). Otherwise don't slow down.
                    if self.velocity / car_distance_to_tl < 1.5:
                        slow_down = True
                        if self.car_distance_to_tl_when_car_started_to_slow_down is None:
                            self.car_distance_to_tl_when_car_started_to_slow_down = car_distance_to_tl
                            self.car_velocity_when_car_started_to_slow_down = self.velocity
                        #rospy.loginfo('Stopping the car')

            # Fill the lane with the final waypoints
            for num_wp in range(LOOKAHEAD_WPS):
                wp = Waypoint()
                wp.pose = waypoints.waypoints[(first_wpt_index + num_wp) % num_waypoints_in_list].pose
                wp.twist = waypoints.waypoints[(first_wpt_index + num_wp) % num_waypoints_in_list].twist

                # Calculate velocity
                if slow_down and reached_zero_velocity:
                    # If we already reached zero velocity, just set the value and skip the calculation
                    wp.twist.twist.linear.x = 0.0
                elif slow_down:
                    # Calculate the distance between the waypoint and the traffic light's stop line
                    wp_distance_to_tl = self.distance(wp.pose.pose.position, light_waypoint.pose.pose.position) - self.light_distance_thresh
                    # Calculate the distance between the car and the waypoint
                    car_distance_to_wp = self.distance(self.pose.pose.position, wp.pose.pose.position)

                    # Stop the car in a safe distance before the stop line to give the simulator space to adapt velocity
                    if car_distance_to_tl > wp_distance_to_tl and car_distance_to_tl > car_distance_to_wp and wp_distance_to_tl > 4:
                        # Estimate what speed the car should have at the waypoint
                        velocity_fraction = wp_distance_to_tl / self.car_distance_to_tl_when_car_started_to_slow_down
                        if velocity_fraction > 0 and velocity_fraction <= 1:
                            # Slowing down
                            wp.twist.twist.linear.x = velocity_fraction * self.car_velocity_when_car_started_to_slow_down
                        else:
                            # The car is already beyond the traffic light
                            wp.twist.twist.linear.x = self.default_velocity
                        # Drive with a minimum speed until we reach the stop line
                        if wp.twist.twist.linear.x < 1.0:
                            wp.twist.twist.linear.x = 1.0
                    elif car_distance_to_tl < wp_distance_to_tl:
                        # The waypoint is somewhere beyond the traffic light
                        wp.twist.twist.linear.x = self.default_velocity
                    else:
                        wp.twist.twist.linear.x = 0.0

                    if wp.twist.twist.linear.x == 0.0:
                        reached_zero_velocity = True
                else:
                    self.car_distance_to_tl_when_car_started_to_slow_down = None
                    self.car_velocity_when_car_started_to_slow_down = None
                    wp.twist.twist.linear.x = self.default_velocity

                wp.twist.twist.linear.y = 0.0
                wp.twist.twist.linear.z = 0.0
                wp.twist.twist.angular.x = 0.0
                wp.twist.twist.angular.y = 0.0
                wp.twist.twist.angular.z = 0.0
                lane.waypoints.append(wp)

        # finally, publish waypoints as modified on /final_waypoints topic
        self.final_waypoints_pub.publish(lane)

    def traffic_cb(self, traffic_waypoint):
        # Callback for /traffic_waypoint message.
        # Store the timestamp and the traffic light position to use them for final_waypoints in waypoints_cb
        self.traffic_waypoint_timestamp = time.time()
        self.light_waypoint_index = traffic_waypoint.data

    def obstacle_cb(self, msg):
        # TODO: Callback for /obstacle_waypoint message. We will implement it later
        pass

    def get_waypoint_velocity(self, waypoint):
        return waypoint.twist.twist.linear.x

    def set_waypoint_velocity(self, waypoints, waypoint, velocity):
        waypoints[waypoint].twist.twist.linear.x = velocity

    def distance(self, waypoints, wp1, wp2):
        dist = 0
        dl = lambda a, b: math.sqrt((a.x-b.x)**2 + (a.y-b.y)**2  + (a.z-b.z)**2)
        for i in range(wp1, wp2+1):
            dist += dl(waypoints[wp1].pose.pose.position, waypoints[i].pose.pose.position)
            wp1 = i
        return dist

    def distance(self, pose1, pose2):
        return math.sqrt((pose1.x-pose2.x)**2 + (pose1.y-pose2.y)**2  + (pose1.z-pose2.z)**2)

if __name__ == '__main__':
    try:
        WaypointUpdater()
    except rospy.ROSInterruptException:
        rospy.logerr('Could not start waypoint updater node.')

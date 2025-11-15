// ReSpeaker Mic Array v3.0 — inset plate, rotatable holes & feet,
// with deep base-mount recesses fully inside a thicker plate.

// ====== MAIN DIMENSIONS ======
plate_outer_d    = 80;      // your OD
plate_thickness  = 17.0;     // go to 6.0 if you want bigger recesses

// PCB recess pocket
pocket_d         = 76;      // ~board OD + 0.3–0.6
pocket_depth     = 14;     // how far the PCB seats down

// 3-hole board pattern (120° apart)
bolt_circle_d    = 45;
hole_clearance   = 2.8;     // 2.8 M2.5, 3.2 M3
cbore_d          = 5.2;     // board-screw counterbore (0 to disable)
cbore_depth      = 1.0;

vent_circle_d    = 55;
vent_holes       = 12;
vent_clearance   = 5.5;

// Independent rotations
mount_offset_deg = 60;   // 3-hole PCB pattern
feet_offset_deg  = 30;   // feet only
base_mount_offset_deg = 45; // base-mount holes only

// Cable notch (fixed orientation)
add_cable_notch  = true;
cable_notch_w    = 25;
cable_notch_d    = 8;

pocket_chamfer = 0.3;   // 0.2–0.4 is a nice range for PETG

// How high the notch should start above the bottom (i.e., your base plate thickness)
base_plate_thickness = plate_outer_d - pocket_d;

// ====== BASE MOUNTS (for fastening this base to something below) ======
add_base_mounts          = false;
base_mount_use_feet_radius = true;   // true: same radius as feet, false: use circle below
base_standoff_count      = 4;        // number of base-mount holes
base_standoff_circle_d   = 50;       // used when base_mount_use_feet_radius = false
base_hole_d              = 3.2;

use_base_countersink     = false;    // or true for 90° flat-heads
base_cbore_d             = 6.6;
base_cbore_depth_top     = 2.4;
base_csink_d_top         = 6.0;
base_csink_angle         = 90;

$fn = 128;

// --- SLIP-FIT COVER SKIRT (for sliding over the base) ---
base_lip_outer_d   = 76;   // OUTER diameter of the base at its widest lip (measure)
base_lip_radial    = 1.5;  // how far that lip sticks out beyond the base body (mm, radial)
slip_clearance_rad = 0.25; // radial clearance for PETG slip fit (0.2–0.35 is typical)
skirt_wall_thick   = 2.0;  // wall thickness of the cover’s skirt
skirt_height       = 5.0;  // how far the skirt extends DOWN past the plate (you asked ~5mm)

// Derived: skirt inner/outer diameters and overall cover OD
skirt_inner_d = (base_lip_outer_d + 2*base_lip_radial) + 2*slip_clearance_rad;
skirt_outer_d = skirt_inner_d + 2*skirt_wall_thick;
cover_outer_d = max(plate_outer_d, skirt_outer_d);

// ====== SAFETY GUARDS ======
min_bottom          = 1.2;  // leave at least this much under the deepest pocket
pocket_depth_       = min(pocket_depth, plate_thickness - min_bottom);
cbore_depth_        = min(cbore_depth, plate_thickness - 0.6);
base_cbore_depth_   = min(base_cbore_depth_top, plate_thickness - min_bottom);

// ====== HELPERS ======
// Downward skirt so the cover slips over the base; notch will live only in this skirt
module slip_skirt() {
  translate([0,0,-skirt_height])  // uses your existing skirt_height (a.k.a. slip_len)
    difference() {
      cylinder(d = skirt_outer_d, h = skirt_height, $fn=128);
      translate([0,0,-0.1]) cylinder(d = skirt_inner_d, h = skirt_height+0.2, $fn=128);
    }
}
// ---------- Teardrop vent helpers ----------
module teardrop2d(d=3, tip_len=1.2) {
  // Circle + little triangular cap (pointing +Y in local coords)
  union() {
    circle(d=d, $fn=48);
    polygon(points=[
      [-d/2, 0],
      [ d/2, 0],
      [   0, d/2 + tip_len]
    ]);
  }
}
module teardrop_hole(d=3, h=10, tip_len=1.2, orient_deg=0) {
  rotate([0,0,orient_deg])
    linear_extrude(height=h, center=false, convexity=10)
      teardrop2d(d, tip_len);
}

// Optional: tiny chamfer ring to deburr PETG on both faces
module chamfer_ring(d=3, c=0.3, h=0.2) {
  translate([0,0,-0.01]) cylinder(d=d + 2*c, h=h+0.02, $fn=48);
}

// Place N teardrops on a circle, tips pointing outward
module teardrops_on_circle(n, d_circle, vent_d, plate_h,
                           rot_deg=0, tip_len=1.2,
                           use_chamfer=true, chamfer=0.3) {
  if (n > 0 && d_circle > 0)  // guards
  rotate([0,0,rot_deg]) {
    for (i = [0:n-1]) {
      a = 360/n * i;
      translate([ (d_circle/2)*cos(a), (d_circle/2)*sin(a), -0.1 ]) {
        teardrop_hole(d=vent_d, h=plate_h+0.2, tip_len=tip_len, orient_deg=a);
        if (use_chamfer && chamfer > 0) {
          chamfer_ring(d=vent_d, c=chamfer, h=0.2);                     // bottom
          translate([0,0,plate_h-0.2]) chamfer_ring(d=vent_d, c=chamfer, h=0.2); // top
        }
      }
    }
  }
}
module holes_on_circle(n, d_circle, hole_d, h, rot_deg=0) {
  rotate([0,0,rot_deg]) for (i=[0:n-1]) {
    a = 360/n * i;
    translate([ (d_circle/2)*cos(a), (d_circle/2)*sin(a), -0.1 ])
      cylinder(d=hole_d, h=h+0.2, $fn=48);
  }
}

module counterbores_on_circle(n, d_circle, bore_d, bore_h, z0, rot_deg=0) {
  if (bore_d>0 && bore_h>0)
  rotate([0,0,rot_deg]) for (i=[0:n-1]) {
    a = 360/n * i;
    translate([ (d_circle/2)*cos(a), (d_circle/2)*sin(a), z0 ])
      cylinder(d=bore_d, h=bore_h+0.2, $fn=64);
  }
}

module countersinks_on_circle(n, d_circle, d_top, angle_deg, z0, h_max, rot_deg=0) {
  // Approximate a countersink with a cone (linear_extrude of a circle via scale)
  rotate([0,0,rot_deg]) for (i=[0:n-1]) {
    a = 360/n * i;
    // height from top surface down to where the cone meets the shank
    h = min(h_max, plate_thickness - min_bottom);
    translate([ (d_circle/2)*cos(a), (d_circle/2)*sin(a), z0 ])
      cylinder(h=h+0.2, d1=d_top, d2=max(0.1, d_top - 2*h*tan((angle_deg/2)*PI/180)), $fn=64);
  }
}

function foot_radial(r_plate, r_foot) = r_plate - foot_edge_margin - r_foot;

module cable_notch() {
  if (add_cable_notch) {
    // Notch begins at Z = base_plate_thickness, so it does NOT cut through the teardrop layer
    translate([ (plate_outer_d/2) - cable_notch_d, -cable_notch_w/2, base_plate_thickness ])
      cube([ cable_notch_d + 0.2,
             cable_notch_w,
             plate_thickness - base_plate_thickness + 0.2 ]);
  }
}

// --- put this UP TOP with your other helpers ---
module sink_at(x, y) {
  // depth limited to keep bottom skin >= min_bottom
  cs_depth = min(plate_thickness - min_bottom, plate_thickness - 0.6);
  // bottom diameter from top diameter and angle
  d_bot = max(0.1, base_csink_d_top - 2*cs_depth*tan((base_csink_angle/2)*PI/180));
  translate([x, y, plate_thickness - cs_depth])
    cylinder(h = cs_depth + 0.2, d1 = base_csink_d_top, d2 = d_bot, $fn=64);
}

// Replace your base_mounts() with this:
module base_mounts() {
  if (add_base_mounts) {

    r_plate = plate_outer_d/2;
    r_foot  = max(foot_d/2, 0.1);
    radial_feet = foot_radial(r_plate, r_foot);

    if (base_mounts_follow_feet) {
      rotate([0,0,feet_offset_deg]) {
        for (i=[0:foot_count-1]) {
          a   = 360/foot_count * i;
          x   = radial_feet*cos(a);
          y   = radial_feet*sin(a);

          // through-hole
          translate([x, y, -0.1])
            cylinder(d = base_hole_d,
                     h = plate_thickness + foot_height + 0.3, $fn=48);

          // head treatment (entirely inside the thicker plate)
          if (use_base_countersink) {
            sink_at(x, y);
          } else {
            translate([x, y, plate_thickness - base_cbore_depth_])
              cylinder(d = base_cbore_d, h = base_cbore_depth_ + 0.2, $fn=64);
          }
        }
      }
    } else {
      // Independent circle for base mounts
      holes_on_circle(base_standoff_count, base_standoff_circle_d,
                      base_hole_d, plate_thickness + foot_height, base_mount_offset_deg);

      if (use_base_countersink) {
        // place individual sinks at the same circle
        rotate([0,0,base_mount_offset_deg]) {
          for (i=[0:base_standoff_count-1]) {
            a = 360/base_standoff_count * i;
            x = (base_standoff_circle_d/2)*cos(a);
            y = (base_standoff_circle_d/2)*sin(a);
            sink_at(x, y);
          }
        }
      } else {
        counterbores_on_circle(base_standoff_count, base_standoff_circle_d,
          base_cbore_d, base_cbore_depth_,
          plate_thickness - base_cbore_depth_, base_mount_offset_deg);
      }
    }
  }
}

// ====== MODEL ======
module plate_inset() {
  difference() {
    // main disk
    cylinder(d=plate_outer_d, h=plate_thickness);

    // PCB recess pocket
    translate([0,0,plate_thickness - pocket_depth_])
      cylinder(d=pocket_d, h=pocket_depth_ + 0.2);
    // pocket lip chamfer (top edge of the recess)
    translate([0,0,plate_thickness - pocket_depth_ - pocket_chamfer])
      cylinder(d = pocket_d + 2*pocket_chamfer,
               h = pocket_chamfer + 0.2, $fn=128);

    // 3-hole board mounts + counterbores
    // holes_on_circle(12, bolt_circle_d, hole_clearance, plate_thickness, mount_offset_deg);
      
    // teardrop vent rings (tips point outward, bridge cleanly)
    for (i=[0:3]) {
      teardrops_on_circle(
        vent_holes - 4*i,                  // count per ring
        vent_circle_d - 15*i,              // circle diameter per ring
        vent_clearance,                    // “round” part of the teardrop
        plate_thickness,                   // hole height
        mount_offset_deg,                  // keep your rotation
        tip_len = vent_clearance * 0.6,    // good starting bridge length
        use_chamfer = true,
        chamfer = 0.3
      );
    }

    // optional center vent — use a single teardrop or a plain circle (no zero-diameter!)
    //translate([0,0,-0.1]) teardrop_hole(d=vent_clearance, h=plate_thickness+0.2,
    //                                 tip_len=vent_clearance*0.6, orient_deg=0);
    // Or, if you prefer a round center:
    translate([0,0,-0.1]) cylinder(d=vent_clearance, h=plate_thickness+0.2, $fn=48);
    
    counterbores_on_circle(3, bolt_circle_d, cbore_d, cbore_depth_,
                           plate_thickness - cbore_depth_, mount_offset_deg);

    // Base mounts (through + recessed heads)
    base_mounts();

    // Cable notch
    cable_notch();
  }
}

// Assemble
union() {
  plate_inset();
}

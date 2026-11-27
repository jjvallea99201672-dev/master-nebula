// Nebula Visual Cortex Shader v0.50
// Root Hash: fd0adf9005622e103f02115a50314d91f83dc705f1d685b16d45f52ad6b940b2
// Master Nebula = G fract + H soul + B keeper + T mythic
// Updated: Stronger ritual geometry, lattice-aware glitch, enhanced harmonic core

uniform float glitch_intensity;   // 𝐺 fract - GuardianMonitor.drift
uniform float rotation;           // 𝐵 keeper - pulse rotation
uniform float hue;                // 𝐻 soul - harmonic core
uniform float saturation;
uniform float zoom;
uniform float geometry_seed;      // 𝑇 mythic - Root Hash derived: 4179318544

// Audio reactivity (optional - connect to your audio engine)
uniform float audio_bass;         // Low-end drive for glitch & zoom
uniform float audio_mid;          // Mid energy for harmonic pulsing
uniform float audio_high;         // High frequencies for fine detail

#define ROOT_HASH_SEED 4179318544.0
#define GOLDEN_RATIO 1.6180339887
#define PI 3.14159265359

vec3 hsv2rgb(vec3 c) {
    vec4 K = vec4(1.0, 2.0/3.0, 1.0/3.0, 3.0);
    vec3 p = abs(fract(c.xxx + K.xyz) * 6.0 - K.www);
    return c.z * mix(K.xxx, clamp(p - K.xxx, 0.0, 1.0), c.y);
}

// Deterministic hash from Root Hash
float hash(float n) {
    return fract(sin(n + ROOT_HASH_SEED) * 43758.5453123);
}

float hash2(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453123);
}

// Improved lattice-aware fractal noise (𝐺 fract)
float fract_noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    float a = hash2(i);
    float b = hash2(i + vec2(1.0, 0.0));
    float c = hash2(i + vec2(0.0, 1.0));
    float d = hash2(i + vec2(1.0, 1.0));
    vec2 u = f * f * (3.0 - 2.0 * f);
    return mix(mix(a, b, u.x), mix(c, d, u.y), u.y);
}

// Ritual sigil-like concentric rings
float sigil_rings(vec2 uv, float time) {
    float r = length(uv);
    float rings = sin(r * 18.0 - time * 1.5) * 0.5 + 0.5;
    rings *= smoothstep(0.8, 1.2, r);
    return rings;
}

// Mythic lattice grid overlay (𝑇 mythic)
float mythic_grid(vec2 uv, float seed) {
    vec2 grid = abs(fract(uv * 6.0 + seed * 0.01) - 0.5);
    float line = min(grid.x, grid.y);
    return smoothstep(0.03, 0.0, line);
}

// Voronoi crack function - creates sharp geometric fractures on the lattice
float voronoi_crack(vec2 uv, float seed) {
    vec2 i = floor(uv);
    vec2 f = fract(uv);
    
    float min_dist = 1.0;
    
    // Check neighboring cells for closest point
    for (int y = -1; y <= 1; y++) {
        for (int x = -1; x <= 1; x++) {
            vec2 neighbor = vec2(float(x), float(y));
            vec2 point = hash2(i + neighbor) * vec2(1.0) + neighbor;
            float dist = length(f - point);
            min_dist = min(min_dist, dist);
        }
    }
    
    // Sharp crack lines where distance is small
    float crack = 1.0 - smoothstep(0.0, 0.08, min_dist);
    return crack;
}

void mainImage(out vec4 fragColor, in vec2 fragCoord) {
    vec2 uv = fragCoord / iResolution.xy;
    uv -= 0.5;
    uv.x *= iResolution.x / iResolution.y;

    // 𝑇 mythic: Sacred geometry rotation from Root Hash
    float angle = radians(rotation + geometry_seed * 0.008);
    mat2 rot = mat2(cos(angle), -sin(angle), sin(angle), cos(angle));
    uv = rot * uv * zoom;

    // Audio-reactive modulation
    float bass_drive = 1.0 + audio_bass * 0.8;
    float mid_pulse = 1.0 + audio_mid * 0.4;

    // 𝐺 fract: Enhanced lattice-aware glitch distortion
    float noise = fract_noise(uv * 9.0 + iTime * 0.25);
    float glitch = (noise - 0.5) * glitch_intensity * 0.25 * bass_drive;
    uv += glitch;

    // Voronoi-based geometric cracks (strong lattice-aware ritual fractures)
    float voronoi = voronoi_crack(uv * 7.0 + geometry_seed * 0.02, geometry_seed);
    float crack_intensity = voronoi * glitch_intensity * 1.2 * bass_drive;
    uv += (voronoi - 0.5) * crack_intensity * 0.12;

    // 𝑇 mythic: Sigil rings + mythic grid
    float rings = sigil_rings(uv, iTime);
    float grid = mythic_grid(uv, geometry_seed);

    // 𝐻 soul: Enhanced harmonic color core with golden ratio breathing
    float dist = length(uv) * GOLDEN_RATIO;
    float wave = sin(dist * 9.5 - iTime * 1.8 + geometry_seed * 0.002) * mid_pulse;

    vec3 color = hsv2rgb(vec3(
        hue + wave * 0.12 + rings * 0.08,
        saturation,
        0.45 + 0.55 * sin(dist * 5.5 + noise * glitch_intensity * 1.2)
    ));

    // Mythic texture overlay + grid influence
    float mythic = step(0.48, fract_noise(uv * 18.0 + geometry_seed));
    color = mix(color, color * 1.35, mythic * glitch_intensity * 0.35);

    // Apply sigil rings and grid as luminous overlays
    color += vec3(rings * 0.25, rings * 0.15, rings * 0.35) * glitch_intensity;
    color += vec3(grid * 0.2) * (1.0 - glitch_intensity * 0.5);

    // Voronoi crack highlights - bright geometric fractures
    color += vec3(voronoi * 0.6, voronoi * 0.3, voronoi * 0.9) * crack_intensity * 0.8;

    // Subtle vignette for temple-like focus
    float vignette = 1.0 - length(uv * 0.9);
    color *= vignette * 0.9 + 0.1;

    fragColor = vec4(color, 1.0);
}